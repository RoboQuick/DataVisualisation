# Required libaries
# PyQt5 libaries
from PyQt5.QtGui import *
from PyQt5.QtWidgets import QWidget, QGridLayout
from PyQt5.QtCore import QTimer
from pyqtgraph import AxisItem
from pyqtgraph import QtWidgets
import pyqtgraph as pg
from PyQt5.QtWebEngineWidgets import QWebEngineView

# Rest of the libaries
import numpy
import sys
import os
import threading
import serial
import random
from datetime import datetime, timedelta
import time
from time import mktime
import folium
import io
import csv
from jinja2 import Template


# String for receiving serial data
base_station_data = ""

# Raw data - all data that comes in from serial port, it can be corrupted
# It can still be useful to get data, even if not everything can be used
raw_data = []

# Displayed data - data that shouldn't be corrupted
# Non-corrupted data received from serial port is appended to this list
# Latest data is split and gets appended to data lists, to be used by graphs
displayed_data = []

# Serial port being uses
# The new school laptop uses COM4, my computer uses COM8
com_port = "COM4"

file_name = f"data{random.randint(1000, 10000)}.csv"
with open(file_name, "a", newline="", encoding="UTF8") as csv_f:
           writer = csv.writer(csv_f)
           writer.writerow(["Time","Latititude","Longitude","Speed","Altitude","Temp","Humidity","Pressure","eCO2","CO2","TVOC","NO2","PM10","PM25","PM100","RSSI","SNR"])

# This class makes it possible for graphs to display time as x-axis
# Don't touch this class, it works as it should.
class DateAxisItem(AxisItem):
    # Max width in pixels reserved for each label in axis
    _pxLabelWidth = 80

    def __init__(self, *args, **kwargs):
        AxisItem.__init__(self, *args, **kwargs)
        self._oldAxis = None

    def tickValues(self, minVal, maxVal, size):
        maxMajSteps = int(size/self._pxLabelWidth)

        dt1 = datetime.fromtimestamp(minVal)
        dt2 = datetime.fromtimestamp(maxVal)

        dx = maxVal - minVal
        majticks = []

        if dx > 63072001:  # 3600s*24*(365+366) = 2 years (count leap year)
            d = timedelta(days=366)
            for y in range(dt1.year + 1, dt2.year):
                dt = datetime(year=y, month=1, day=1)
                majticks.append(mktime(dt.timetuple()))

        elif dx > 5270400:  # 3600s*24*61 = 61 days
            d = timedelta(days=31)
            dt = dt1.replace(day=1, hour=0, minute=0,
                             second=0, microsecond=0) + d
            while dt < dt2:
                # make sure that we are on day 1 (even if always sum 31 days)
                dt = dt.replace(day=1)
                majticks.append(mktime(dt.timetuple()))
                dt += d

        elif dx > 172800:  # 3600s24*2 = 2 days
            d = timedelta(days=1)
            dt = dt1.replace(hour=0, minute=0, second=0, microsecond=0) + d
            while dt < dt2:
                majticks.append(mktime(dt.timetuple()))
                dt += d

        elif dx > 7200:  # 3600s*2 = 2hours
            d = timedelta(hours=1)
            dt = dt1.replace(minute=0, second=0, microsecond=0) + d
            while dt < dt2:
                majticks.append(mktime(dt.timetuple()))
                dt += d

        elif dx > 1200:  # 60s*20 = 20 minutes
            d = timedelta(minutes=10)
            dt = dt1.replace(minute=(dt1.minute // 10) * 10,
                             second=0, microsecond=0) + d
            while dt < dt2:
                majticks.append(mktime(dt.timetuple()))
                dt += d

        elif dx > 120:  # 60s*2 = 2 minutes
            d = timedelta(minutes=1)
            dt = dt1.replace(second=0, microsecond=0) + d
            while dt < dt2:
                majticks.append(mktime(dt.timetuple()))
                dt += d

        elif dx > 20:  # 20s
            d = timedelta(seconds=10)
            dt = dt1.replace(second=(dt1.second // 10) * 10, microsecond=0) + d
            while dt < dt2:
                majticks.append(mktime(dt.timetuple()))
                dt += d

        elif dx > 2:  # 2s
            d = timedelta(seconds=1)
            majticks = range(int(minVal), int(maxVal))

        else:  # <2s , use standard implementation from parent
            return AxisItem.tickValues(self, minVal, maxVal, size)

        L = len(majticks)
        if L > maxMajSteps:
            majticks = majticks[::int(numpy.ceil(float(L) / maxMajSteps))]

        return [(d.total_seconds(), majticks)]

    def tickStrings(self, values, scale, spacing):
        ret = []
        if not values:
            return []

        if spacing >= 31622400:  # 366 days
            fmt = "%Y"

        elif spacing >= 2678400:  # 31 days
            fmt = "%Y %b"

        elif spacing >= 86400:  # = 1 day
            fmt = "%b/%d"

        elif spacing >= 3600:  # 1 h
            fmt = "%b/%d-%Hh"

        elif spacing >= 60:  # 1 m
            fmt = "%H:%M"

        elif spacing >= 1:  # 1s
            fmt = "%H:%M:%S"

        else:
            # less than 2s (show microseconds)
            # fmt = '%S.%f"'
            fmt = '[+%fms]'  # explicitly relative to last second

        for x in values:
            try:
                t = datetime.fromtimestamp(x)
                ret.append(t.strftime(fmt))
            except ValueError:  # Windows can't handle dates before 1970
                ret.append('')

        return ret

    def attachToPlotItem(self, plotItem):
        pen_line = pg.mkPen(color=(0, 0, 0), width=3)
        self.setParentItem(plotItem)
        viewBox = plotItem.getViewBox()
        self.linkToView(viewBox)
        self._oldAxis = plotItem.axes[self.orientation]['item']
        self._oldAxis.hide()
        plotItem.axes[self.orientation]['item'] = self
        pos = plotItem.axes[self.orientation]['pos']
        plotItem.layout.addItem(self, *pos)
        self.setZValue(-1000)
        plotItem.getAxis('bottom').setPen(pen_line)

    def detachFromPlotItem(self):
        raise NotImplementedError()  # TODO


# Main window
class Window(QWidget):
    def __init__(self):
        super().__init__()
        # Lists where all data is stored
        self.timestamps = []
        self.data_latitude = []
        self.data_longitude = []
        self.data_speed = []
        self.data_temp = []
        self.data_humid = []
        self.data_alt = []
        self.data_press = []
        self.data_eco2 = []
        self.data_co2 = []
        self.data_tvoc = []
        self.data_no2 = []
        self.data_pm10 = []
        self.data_pm25 = []
        self.data_pm100 = []
        self.data_rssi = []
        self.data_snr = []

        # Starts a timer that updates lists and the graphs every second
        self.qTimer = QTimer()
        self.qTimer.setInterval(1000)  # milliseconds
        self.qTimer.start()
        self.qTimer.timeout.connect(self.update_data_real)

        # Run the ui
        self.initUI()

    # For use with base station itself
    # For this function to work it needs to be set above in qTimer.timeout
    # and also base station has to be connected to PC and
    # Cansat has to be transmitting data
    def update_data_real(self):
        try:
            # Splits last received data
            split_data = displayed_data[-1].split(",")
            # Converts every data unit to float
            split_data = [float(x) for x in split_data]
            # Gets time when data was received
            self.timestamps.append(time.time())
            # Appends split data to data lists
            self.data_latitude.append(split_data[0])
            self.data_longitude.append(split_data[1])
            self.data_speed.append(split_data[2])
            self.data_alt.append(split_data[3])
            self.data_temp.append(split_data[4])
            self.data_humid.append(split_data[5])
            self.data_press.append(split_data[6])
            self.data_eco2.append(split_data[7])
            self.data_co2.append(split_data[8])
            self.data_tvoc.append(split_data[9])
            self.data_no2.append(split_data[10])
            self.data_pm10.append(split_data[11])
            self.data_pm25.append(split_data[12])
            self.data_pm100.append(split_data[13])
            self.data_rssi.append(split_data[14])
            self.data_snr.append(split_data[15])

            # Makes sure that it doesn't try to change data to lists with no values
            if len(self.data_latitude) > 0:
                # Updates all graphs with new data
                self.temp_plot.setData(self.timestamps, self.data_temp)
                self.press_plot.setData(self.timestamps, self.data_press)
                self.humid_plot.setData(self.timestamps, self.data_humid)
                self.alt_plot.setData(self.timestamps, self.data_alt)
                self.spd_plot.setData(self.timestamps, self.data_speed)
                self.co2_plot_line.setData(self.timestamps, self.data_co2)
                self.eco2_plot_line.setData(self.timestamps, self.data_eco2)
                self.tvoc_plot_line.setData(self.timestamps, self.data_tvoc)
                self.no2_plot_line.setData(self.timestamps, self.data_no2)
                self.pm10_plot_line.setData(self.timestamps, self.data_pm10)
                self.pm25_plot_line.setData(self.timestamps, self.data_pm25)
                self.pm100_plot_line.setData(self.timestamps, self.data_pm100)
                # If GPS signal is acquired add a marker in the map
                if int(self.data_latitude[-1]) != 0:
                    #self.add_marker()
                    pass

                # Prints received data to consoles in app
                self.raw_console.append(raw_data[-1])
                self.displayed_console.append(displayed_data[-1])
        except:
            pass

    # Function that can put a marker on map
    def add_marker(self):
        js = Template(
            """
        L.marker([{{latitude}}, {{longitude}}] )
            .addTo({{map}});
        L.circleMarker(
            [{{latitude}}, {{longitude}}], {
                "bubblingMouseEvents": true,
                "color": "#3388ff",
                "dashArray": null,
                "dashOffset": null,
                "fill": false,
                "fillColor": "#3388ff",
                "fillOpacity": 0.2,
                "fillRule": "evenodd",
                "lineCap": "round",
                "lineJoin": "round",
                "opacity": 1.0,
                "radius": 2,
                "stroke": true,
                "weight": 5
            }
        ).addTo({{map}});
        """
        ).render(map=self.map.get_name(), latitude=self.data_latitude[-1], longitude=self.data_longitude[-1])
        self.mapView.page().runJavaScript(js)

    # Function that adds all gui elements
    def initUI(self):
        grid = QGridLayout()
        self.setLayout(grid)
        self.setWindowTitle("Base station data")
        self.setAutoFillBackground(True)
        pg.setConfigOptions(antialias=True)
        p = self.palette()
        # Here it is possible to change background colour of app
        p.setColor(self.backgroundRole(), QColor(255, 255, 255))
        self.setPalette(p)

        # Creates new text blocks to be used as consoles to display received data
        self.raw_console = QtWidgets.QTextEdit()
        self.displayed_console = QtWidgets.QTextEdit()

        # Makes consoles wider
        self.raw_console.setFixedWidth(700)
        self.displayed_console.setFixedWidth(700)
        self.raw_console.setFixedHeight(200)

        # Creater a map to display GPS coordinates
        self.map = folium.Map(location=[57, 25], zoom_start=8, control_scale=True)
        folium.LayerControl().add_to(self.map)
        self.data = io.BytesIO()
        self.map.save(self.data, close_file=False)
        self.mapView = QWebEngineView()
        self.mapView.setHtml(self.data.getvalue().decode())

        # Creates graph widgets
        self.temperature_plot = pg.PlotWidget()
        self.pressure_plot = pg.PlotWidget()
        self.humidity_plot = pg.PlotWidget()
        self.altitude_plot = pg.PlotWidget()
        self.speed_plot = pg.PlotWidget()
        self.co2_plot = pg.PlotWidget()
        self.tvoc_plot = pg.PlotWidget()
        self.no2_plot = pg.PlotWidget()
        self.pms_plot = pg.PlotWidget()

        # Sets y-axis labels
        self.temperature_plot.setLabel(axis="left", text="Temperature, Celsius")
        self.pressure_plot.setLabel(axis="left", text="Pressure, Pa")
        self.humidity_plot.setLabel(axis="left", text="Humidity, %")
        self.altitude_plot.setLabel(axis="left", text="Altitude, meters")
        self.speed_plot.setLabel(axis="left", text="Speed, km/h")
        self.co2_plot.setLabel(axis="left", text="Co2 concentration, ppm")
        self.tvoc_plot.setLabel(axis="left", text="TVOC concentration, ug/m^3")
        self.no2_plot.setLabel(axis="left", text="NO2 concentration, ppm")
        self.pms_plot.setLabel(axis="left", text="Fine particles, ppm")

        # Changes background of graphs to the same colour as app
        self.temperature_plot.setBackground(None)
        self.pressure_plot.setBackground(None)
        self.humidity_plot.setBackground(None)
        self.altitude_plot.setBackground(None)
        self.speed_plot.setBackground(None)
        self.co2_plot.setBackground(None)
        self.tvoc_plot.setBackground(None)
        self.no2_plot.setBackground(None)
        self.pms_plot.setBackground(None)

        # Adds legends
        self.co2_plot.addLegend()
        self.pms_plot.addLegend()

        # Changes y-axis colour to black and makes lines wider
        # To change x-axis colour, check attachToPlotItem funtion in DateAxis class
        pen_line = pg.mkPen(color=(0, 0, 0), width=3)
        self.temperature_plot.plotItem.getAxis('left').setPen(pen_line)
        self.pressure_plot.plotItem.getAxis('left').setPen(pen_line)
        self.humidity_plot.plotItem.getAxis('left').setPen(pen_line)
        self.altitude_plot.plotItem.getAxis('left').setPen(pen_line)
        self.speed_plot.plotItem.getAxis('left').setPen(pen_line)
        self.co2_plot.plotItem.getAxis('left').setPen(pen_line)
        self.tvoc_plot.plotItem.getAxis('left').setPen(pen_line)
        self.no2_plot.plotItem.getAxis('left').setPen(pen_line)
        self.pms_plot.plotItem.getAxis('left').setPen(pen_line)

        # Add the Date-time axis to each graph
        axis1 = DateAxisItem(orientation='bottom')
        axis1.attachToPlotItem(self.temperature_plot.getPlotItem())

        axis2 = DateAxisItem(orientation='bottom')
        axis2.attachToPlotItem(self.pressure_plot.getPlotItem())

        axis3 = DateAxisItem(orientation='bottom')
        axis3.attachToPlotItem(self.humidity_plot.getPlotItem())

        axis4 = DateAxisItem(orientation='bottom')
        axis4.attachToPlotItem(self.altitude_plot.getPlotItem())

        axis5 = DateAxisItem(orientation='bottom')
        axis5.attachToPlotItem(self.speed_plot.getPlotItem())

        axis6 = DateAxisItem(orientation='bottom')
        axis6.attachToPlotItem(self.co2_plot.getPlotItem())

        axis7 = DateAxisItem(orientation='bottom')
        axis7.attachToPlotItem(self.no2_plot.getPlotItem())

        axis8 = DateAxisItem(orientation='bottom')
        axis8.attachToPlotItem(self.pms_plot.getPlotItem())

        axis9 = DateAxisItem(orientation='bottom')
        axis9.attachToPlotItem(self.tvoc_plot.getPlotItem())

        # Plots data to graphs
        self.temp_plot = self.temperature_plot.plot(x=self.timestamps, y=self.data_temp, pen=pg.mkPen('b', width=5))

        self.press_plot = self.pressure_plot.plot(x=self.timestamps, y=self.data_press, pen=pg.mkPen('b', width=5))

        self.humid_plot = self.humidity_plot.plot(x=self.timestamps, y=self.data_humid, pen=pg.mkPen('b', width=5))

        self.alt_plot = self.altitude_plot.plot(x=self.timestamps, y=self.data_alt, pen=pg.mkPen('b', width=5))

        self.spd_plot = self.speed_plot.plot(x=self.timestamps, y=self.data_speed, pen=pg.mkPen('b', width=5))

        self.co2_plot_line = self.co2_plot.plot(x=self.timestamps, y=self.data_co2, name="CO2", pen=pg.mkPen('b', width=5))
        self.eco2_plot_line = self.co2_plot.plot(x=self.timestamps, y=self.data_eco2, name="eCO2", pen=pg.mkPen('g', width=5))

        self.tvoc_plot_line = self.tvoc_plot.plot(x=self.timestamps, y=self.data_tvoc, pen=pg.mkPen('b', width=5))

        self.no2_plot_line = self.no2_plot.plot(x=self.timestamps, y=self.data_no2, pen=pg.mkPen('b', width=5))

        self.pm10_plot_line = self.pms_plot.plot(x=self.timestamps, y=self.data_pm10, name="PM10", pen=pg.mkPen('b', width=5))
        self.pm25_plot_line = self.pms_plot.plot(x=self.timestamps, y=self.data_pm25, name="PM25", pen=pg.mkPen('g', width=5))
        self.pm100_plot_line = self.pms_plot.plot(x=self.timestamps, y=self.data_pm100, name="PM100", pen=pg.mkPen('r', width=5))

        # Adds all widgets to grid
        grid.addWidget(self.temperature_plot, 0, 0)
        grid.addWidget(self.pressure_plot, 1, 0)
        grid.addWidget(self.humidity_plot, 2, 0)

        grid.addWidget(self.speed_plot, 0, 1)
        grid.addWidget(self.altitude_plot, 1, 1)
        grid.addWidget(self.co2_plot, 2, 1)

        grid.addWidget(self.tvoc_plot, 0, 2)
        grid.addWidget(self.no2_plot, 1, 2)
        grid.addWidget(self.pms_plot, 2, 2)

        grid.addWidget(self.raw_console, 3, 2)
        grid.addWidget(self.displayed_console, 4, 2)
        grid.addWidget(self.mapView, 3, 1)

        # Shows the ui
        self.show()


# Checks if received data doesn't have symbols that it shouldn't have
# Doesn't account if one number has changed to another one
# If this check is required, then that has to be set in Cansats and Base station arduino code
# But then we won't receive data if even one charecter has been corrupted
# This way we still can receive data, even if not all of it can be used
def isDataOK():
    allowed_chars = [",", "."]
    for char in base_station_data:
        if not (char.isdigit() or char in allowed_chars):
            return False
    return True



# Connects to serial port and reads data from it
def serialDataFunction():
    base_station = None
    # Opens serial data port
    base_station = serial.Serial("COM4", 9600)
    while True:
        try:
            # If serial connection has been lost, tries to reconnect back
            if(base_station == None):
                base_station = serial.Serial("COM4", 9600)
                print("Reconnecting")
            # Reads data from serial port
            # It blocks function from progressing untill some data has been received
            base_station_data = str(base_station.readline())
            # Removes useless data from string
            base_station_data = base_station_data[2:-6]
            # If data is not corrupted add data to both data lists
            if isDataOK():
                raw_data.append(base_station_data)
                displayed_data.append(base_station_data)
            # If some data has been corrupted, doesn't add it to list that
            # is used to update data to graphs
            else:
                raw_data.append(base_station_data)

            with open(file_name, "a", newline="", encoding="UTF8") as csv_f:
                writer = csv.writer(csv_f)
                writer.writerow([datetime.now().strftime("%H:%M:%S"), base_station_data])
        except:
            # If something goes wrong closes port and tries again
            if(not(base_station == None)):
                base_station.close()
                base_station = None
                print("Disconnecting")

            print("No Connection")
            time.sleep(0.25)


if __name__ == '__main__':
    # Creates a new application process
    app = QtWidgets.QApplication([])
    # Creates the main window
    window = Window()
    serialThread = threading.Thread(target=serialDataFunction, daemon=True)
    serialThread.start()
    # If app is closed, stop running code
    ret = app.exec_()
    sys.exit()
