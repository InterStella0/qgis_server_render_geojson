# -*- coding: utf-8 -*-

"""
***************************************************************************
    render_geojson.py
    ---------------------
    Date                 : May 2020
    Copyright            : (C) 2020 by OPENGIS.ch
    Email                : info@opengis.ch
***************************************************************************
*                                                                         *
*   This program is free software; you can redistribute it and/or modify  *
*   it under the terms of the GNU General Public License as published by  *
*   the Free Software Foundation; either version 2 of the License, or     *
*   (at your option) any later version.                                   *
*                                                                         *
***************************************************************************
"""

__author__ = 'Matthias Kuhn'
__date__ = 'May 2020'
__copyright__ = '(C) 2020, Matthias Kuhn - OPENGIS.ch'

import os
import traceback
import urllib.request

from PyQt5.QtCore import QSize, QByteArray, QBuffer, QIODevice, QEventLoop, Qt, QFile
from PyQt5.QtGui import QColor
from PyQt5.QtXml import QDomDocument
from qgis.core import QgsMapSettings, QgsMapRendererParallelJob, QgsVectorLayer, QgsMessageLog, QgsReadWriteContext, \
    QgsRectangle
from qgis.server import QgsServerFilter


class ParameterError(Exception):
    """A parameter exception that is raised will be forwarded to the client."""
    pass


DPI_CONST = 39.37

class RenderGeojsonFilter(QgsServerFilter):
    def __init__(self, serverIface=None):
        if serverIface:
            super().__init__(serverIface)

        self.prefix_path = os.environ.get('QGIS_RENDERGEOJSON_PREFIX')

    def _resolve_url(self, url):
        """If the path exists locally, relative to the prefix path, return this. If not, try downloading the file."""

        local_path = None
        if self.prefix_path:
            local_path = os.path.join(self.prefix_path, url)

        if not local_path or not os.path.exists(local_path):
            try:
                local_path, _ = urllib.request.urlretrieve(url)
            except ValueError:
                raise ParameterError(f'The file `{url}` could not be found locally and not be retrieved as download.')

        return local_path

    def _load_style(self, layer, style):
        f = QFile(style)
        f.open(QIODevice.ReadOnly)
        d = QDomDocument()
        d.setContent(f)
        doc = d.documentElement()
        rw_context = QgsReadWriteContext()
        layer.readStyle(doc, '', rw_context)

    def handle_requests(self, request):
        params = request.parameterMap()

        try:
            geojson = params['GEOJSON']
        except KeyError:
            raise ParameterError('Parameter GEOJSON must be set.')

        try:
            style = params['STYLE']
        except KeyError:
            raise ParameterError('Parameter STYLE must be set.')

        try:
            width = int(params['WIDTH'])
        except (KeyError, TypeError):
            raise ParameterError('Parameter WIDTH must be integer.')
        try:
            height = int(params['HEIGHT'])
        except (KeyError, TypeError):
            raise ParameterError('Parameter HEIGHT must be integer.')

        try:
            dpi = int(params['DPI'])
        except TypeError:
            raise ParameterError('Parameter DPI must be integer.')
        except KeyError:
            dpi = 96

        try:
            minx, miny, maxx, maxy = params.get('BBOX').split(',')
            bbox = QgsRectangle(float(minx), float(miny), float(maxx), float(maxy))
        except (ValueError, AttributeError):
            raise ParameterError('Parameter BBOX must be specified in the form `min_x,min_y,max_x,max_y`.')

        _resolver = self._resolve_url
        geojson_file_name = _resolver(geojson)

        if '$type' in style:
            polygon_style = _resolver(style.replace('$type', 'polygons'))
            line_style = _resolver(style.replace('$type', 'lines'))
            point_style = _resolver(style.replace('$type', 'points'))
        else:
            point_style = line_style = polygon_style = _resolver(style)

        _style_loader = self._load_style
        polygon_layer = QgsVectorLayer(geojson_file_name + '|geometrytype=Polygon', 'polygons', 'ogr')
        _style_loader(polygon_layer, polygon_style)
        line_layer = QgsVectorLayer(geojson_file_name + '|geometrytype=Line', 'lines', 'ogr')
        _style_loader(line_layer, line_style)
        point_layer = QgsVectorLayer(geojson_file_name + '|geometrytype=Point', 'points', 'ogr')
        _style_loader(point_layer, point_style)

        settings = QgsMapSettings()
        settings.setOutputSize(QSize(width, height))
        settings.setOutputDpi(dpi)
        settings.setExtent(bbox)
        settings.setLayers([polygon_layer, line_layer, point_layer])
        settings.setBackgroundColor(QColor(Qt.transparent))
        renderer = QgsMapRendererParallelJob(settings)

        event_loop = QEventLoop()
        renderer.finished.connect(event_loop.quit)
        renderer.start()

        event_loop.exec_()

        img = renderer.renderedImage()
        img.setDotsPerMeterX(dpi * DPI_CONST)
        img.setDotsPerMeterY(dpi * DPI_CONST)
        image_data = QByteArray()
        buf = QBuffer(image_data)
        buf.open(QIODevice.WriteOnly)
        img.save(buf, 'PNG')

        request.setResponseHeader('Content-type', 'image/png')
        request.appendBody(image_data)

    def responseComplete(self):
        request = self.serverInterface().requestHandler()

        # SERVICE=RENDERGEOJSON -- we are taking over
        if request.parameterMap().get('SERVICE', '').upper() != 'RENDERGEOJSON':
            return

        request.clear()
        try:
            self.handle_requests(request)
        except Exception as e:
            QgsMessageLog.logMessage(f"RenderGeojson.responseComplete :: {type(e).__name__}")
            request.setResponseHeader('Content-type', 'text/plain')
            if isinstance(e, ParameterError):
                request.appendBody(str(e).encode('utf-8'))
                return

            err_trace = traceback.format_exc()
            QgsMessageLog.logMessage(f"RenderGeojson.responseComplete ::  {err_trace}")
            request.appendBody(b'Unhandled error')
            request.appendBody(err_trace.encode('utf-8'))


class RenderGeojsonServer:
    """Render Geojson"""

    def __init__(self, serverIface):
        self.serverIface = serverIface
        serverIface.registerFilter(RenderGeojsonFilter(serverIface), 1)
