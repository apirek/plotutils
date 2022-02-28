#!/usr/bin/env -S python3 -B -OO

# plot - Plot time-series data in realtime
# Copyright (C) 2022  Axel Pirek
# 
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# 
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

import io
import signal
import sys
import time
from argparse import ArgumentParser, ArgumentError
from contextvars import ContextVar
from datetime import datetime
from functools import partial
from threading import Event, Lock, Thread

import numpy as np
import pyqtgraph as pg
from PyQt5 import QtCore, QtGui, QtWidgets

# https://colorbrewer2.org/#type=qualitative
COLOR_SCHEMES = {
    "Dark2": [(27,158,119), (217,95,2), (117,112,179), (231,41,138), (102,166,30), (230,171,2), (166,118,29), (102,102,102)],
    "Pastel1": [(251,180,174), (179,205,227), (204,235,197), (222,203,228), (254,217,166), (255,255,204), (229,216,189), (253,218,236), (242,242,242)],
    "Pastel2": [(179,226,205), (253,205,172), (203,213,232), (244,202,228), (230,245,201), (255,242,174), (241,226,204), (204,204,204)],
    "Set1": [(228,26,28), (55,126,184), (77,175,74), (152,78,163), (255,127,0), (255,255,51), (166,86,40), (247,129,191), (153,153,153)],
    "Set2": [(102,194,165), (252,141,98), (141,160,203), (231,138,195), (166,216,84), (255,217,47), (229,196,148), (179,179,179)],
    "Set3": [(141,211,199), (255,255,179), (190,186,218), (251,128,114), (128,177,211), (253,180,98), (179,222,105), (252,205,229), (217,217,217), (188,128,189), (204,235,197), (255,237,111)],
}

# TODO: Fix auto range mit window

class RelTimeAxisItem(pg.AxisItem):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.autoSIPrefix = False
        self._time_reference = None

    def timeReference(self) -> float:
        return self._time_reference

    def setTimeReference(self, value: float, update=True) -> None:
        self._time_reference = value
        if update:
            self.update()

    def tickValues(self, minVal, maxVal, size):
        assert self._time_reference is not None
        reference = self._time_reference
        # https://github.com/pyqtgraph/pyqtgraph/blob/pyqtgraph-0.11.1/pyqtgraph/graphicsItems/AxisItem.py#L727
        minVal *= self.scale
        maxVal *= self.scale
        ticks = []
        tickLevels = self.tickSpacing(minVal, maxVal, size)
        for spacing, offset in tickLevels:
            start = reference \
                    + np.ceil((reference - minVal) / spacing) * spacing \
                    * (-1 if minVal <= reference else 1)
            n = int(np.ceil((maxVal - start) / spacing) + 1)
            values = (start + np.arange(n) * spacing) / self.scale
            # Remove duplicate ticks
            for _, values_ in ticks:
                values = np.setdiff1d(values, values_, assume_unique=True)
            ticks.append((spacing / self.scale, values))
        return ticks

    def tickStrings(self, values, scale, spacing):
        assert self._time_reference is not None
        reference = self._time_reference
        strings = []
        for value in values:
            value -= reference
            if (value_ := abs(value)) >= 3600:
                string = f"{value // 3600:.0f} h {(value % 3600) // 60:.0f} m"
            elif value_ >= 60:
                string = f"{value // 60:.0f} m {value % 60:.0f} s"
            elif value_ >= 1:
                string = f"{value / 1:.1f} s"
            elif value_ > 0:
                string = f"{value * 1000:.0f} ms"
            elif value_ == 0:
                string = f"0 s"
            strings.append(string)
        return strings


class App(QtGui.QApplication):
    argparser = ArgumentParser(description="Plot time-series data in realtime")
    argparser.add_argument("-d", "--delimiter", default="\t",
            help="The field delimiter")
    argparser.add_argument("-f", "--timefmt", default="%Y-%m-%d %H:%M:%S.%f",
            help="The timestamp format (strptime(3) format string)")
    group = argparser.add_mutually_exclusive_group()
    group.add_argument("-r", "--reltime", default=True, action="store_true", dest="reltime",
            help="Display timestamps relative to the latest")
    group.add_argument("-a", "--abstime", action="store_const", const=False, dest="reltime",
            help="Display timestamps as wall clock")
    argparser.add_argument("-w", "--window", type=float,
            help="Display window (seconds)")
    argparser.add_argument("-x", "--xlabel", default="Zeit",
            help="The X-Axis label")
    #argparser.add_argument("-y", "--ylabel", default=[], action="extend", nargs="+", dest="ylabels",
    #        help="The Y-Axis label")
    argparser.add_argument("-l", "--label", default=[], action="extend", nargs="+", dest="ylabels",
            help="The Y-Axis label")
    argparser.add_argument("-u", "--unit", default=[], action="extend", nargs="+", dest="yunits",
            help="The Y-Axis base SI unit")
    group = argparser.add_mutually_exclusive_group()
    group.add_argument("-s", "--single", default=True, action="store_true", dest="single",
            help="Plot all series in one graph")
    group.add_argument("-m", "--many", action="store_const", const=False, dest="single",
            help="Plot every series in a separate graph")
    # log file

    newData = QtCore.pyqtSignal()
    windowChanged = QtCore.pyqtSignal(float, float)
    timeReferenceChanged = QtCore.pyqtSignal(float)

    def __init__(self, argv: list[str] = []):
        super().__init__(argv)
        self.options = self.argparser.parse_args(self.arguments()[1:])

        # OpenGL.error.Error: Attempt to retrieve context when no valid context
        ## https://stackoverflow.com/a/63869178
        #pg.setConfigOption("useOpenGL", True)
        #pg.setConfigOption("enableExperimental", True)

        self.lock = Lock()
        self.series = None # type: list[list[int]]
        self.plots = None # type: list[pg.PlotDataItem]

        self.newData.connect(self._update)

        #self._update_counter = 0
        #self._update_timer = QtCore.QTimer()
        #def _print(interval):
        #    counter = self._update_counter
        #    self._update_counter = 0
        #    print(f"{counter / interval * 1000:.1f} updates/s")
        #self._update_timer.timeout.connect(partial(_print, 1000))
        #self._update_timer.start(1000)

        self.window = QtGui.QMainWindow()
        self.window.setWindowTitle("plot")
        widget = QtWidgets.QWidget()
        self.window.setCentralWidget(widget)
        layout = QtWidgets.QVBoxLayout(widget)

        self.window.resize(1280, 960)
        self.window.show()

    def addPlots(self, n) -> list[pg.PlotCurveItem]:
        layout = self.window.centralWidget().layout()
        plot_widget = None
        plots = []
        for i in range(n):
            if plot_widget is None or not self.options.single:
                plot_widget = pg.PlotWidget()
                layout.addWidget(plot_widget)
                # Setze Achsen:
                axes = {
                    "left": pg.AxisItem("left"),
                    "right": pg.AxisItem("right"),
                }
                if self.options.reltime:
                    axes |= {
                        "top": RelTimeAxisItem("top", text="the text", units="u"),
                        "bottom": RelTimeAxisItem("bottom"),
                    }
                    for axis in (axes["top"], axes["bottom"]):
                        self.timeReferenceChanged.connect(partial(axis.setTimeReference, update=False))
                else:
                    axes |= {
                        "top": pg.DateAxisItem("top"),
                        "bottom": pg.DateAxisItem("bottom"),
                    }
                plot_widget.setAxisItems(axes)
                # Setze Achsenbeschriftung:
                for axis in ("left", "right"):
                    label = self.options.ylabels[i] if len(self.options.ylabels) > i else ""
                    unit = self.options.yunits[i] if len(self.options.yunits) > i else None
                    plot_widget.setLabel(axis, label, unit)
                for axis in ("top", "bottom"):
                    label = self.options.xlabel
                    plot_widget.setLabel(axis, label)

                plot_widget.showGrid(x=True, y=True)

                if self.options.window:
                    plot_widget.disableAutoRange(pg.ViewBox.XAxis)
                    self.windowChanged.connect(partial(plot_widget.setXRange, update=False))
            if n <= len(COLOR_SCHEMES["Set1"]):
                pen = pg.mkPen(COLOR_SCHEMES["Set1"][i])
            else:
                pen = (i, n)
            #self.plots[i] = pg.PlotDataItem(pen=pen, downsampleMethod="peak", autoDownsample=True, clipToView=True)
            plot = pg.PlotCurveItem(pen=pen)
            plot_widget.addItem(plot)
            plots.append(plot)
        # Verknüpfe X-Achsen.
        # TODO Bessere Implementierung
        # https://github.com/pyqtgraph/pyqtgraph/blob/pyqtgraph-0.11.1/pyqtgraph/graphicsItems/ViewBox/ViewBox.py#L930
        for plot in plots[1:]:
            plot.getViewBox().setXLink(plots[0].getViewBox())
        return plots

    def exec_(self):
        reader = Thread(target=self.read, args=(sys.stdin, ), daemon=True)
        reader.start()
        signal.signal(signal.SIGINT, lambda signum, frame: sys.stdin.close())
        self.aboutToQuit.connect(sys.stdin.close)
        super().exec_()

    def read(self, f: io.TextIOBase):
        delimiter = self.options.delimiter
        timefmt = self.options.timefmt
        strptime = datetime.strptime
        lock = self.lock
        series = self.series
        newData = self.newData
        while True:
            # readline raises ValueError if the file is closed or gets closed during the call.
            try:
                if not (line := f.readline()):
                    break
            except ValueError as e:
                break
            try:
                with lock:
                    fields = line.split(delimiter)
                    if series is None:
                        series = self.series = [[] for _ in fields]
                    series[0].append(strptime(fields[0], timefmt).timestamp())
                    for i, field in enumerate(fields[1:], 1):
                        try:
                            series[i].append(float(field))
                        except ValueError:
                            series[i].append(float("nan"))
            except Exception as e:
                print(f"{e} on line {line}", file=sys.stderr)
            else:
                newData.emit()

    def _update(self):
        assert self.series is not None
        with self.lock:
            if self.plots is None:
                try:
                    self.plots = self.addPlots(len(self.series[1:]))
                except:
                    sys.excepthook(*sys.exc_info())
                    sys.exit(1)
            if self.options.reltime:
                self.timeReferenceChanged.emit(self.series[0][-1])
            if (window := self.options.window):
                xs = self.series[0]
                xmax = xs[-1]
                xmin = xmax - window
                for i in range(len(xs)):
                    if xs[i] >= xmin:
                        break
                for series in self.series:
                    del series[:i]
                self.windowChanged.emit(xmin, xmax)
            for series, plot in zip(self.series[1:], self.plots):
                plot.setData(x=self.series[0], y=series)
        #self._update_counter += 1


if __name__ == "__main__":
    sys.exit(App(sys.argv).exec_())
