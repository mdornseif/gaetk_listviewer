#!/usr/bin/env python
# encoding: utf-8
"""
sui_listviewer.py - Generische Anzeige von Listen.

Dieses Modul implementiert abstrakten Code um Modelle als Listen
anzuzeigen und als XLS zu exportieren. Zuerst in aui_views.py genutzt.
Basiert auf gaetk.modelexporter aus huExpress.

Created by Maximillian Dornseif on 2016-11-11.
Copyright (c) 2016 Cyberlogi. All rights reserved.
"""

import datetime
import re

import gaetk.handler

from gaetk import compat
from gaetk import modelexporter
from google.appengine.api import users

import main_views

from modules import bot
from modules.spezial_ui import sui_models


class ListExportHandler(main_views.HuWaWiHandler):
    """Generischer View zum Anzeigen & Exportieren eines Models als Liste."""

    template = 'sui_listviewer.html'
    title = u'No Title'
    header = []
    row = []
    fields = []  # should be an collections.OrderedDict encoding (header, row)
    filename = None
    required_permission = ['generic_permission']
    required_download_permission = []
    widetable = True
    query = None
    exporter_config = {}

    def get_query(self):
        """Erzeuge Query für Listenansicht"""
        # z.B. `return aui_models.aui_ArtikelUeberverkauf.all().order('frei_max')`
        if callable(self.query):
            return self.query()
        raise NotImplementedError

    def get_pagination(self, query):
        """Paginierung für die HTML-Anzeige auslösen."""
        return self.paginate(query, defaultcount=30, calctotal=True)

    def update_values(self, values):
        """Ermöglicht es, zusätzliche Werte in den Template-Context einzufügen."""
        pass

    def get_headertext(self):
        """Kann überschrieben werden, um einen zusätzlichen Text im Header anzuzeigen."""
        # wird als jinja-Template erzeugt.
        return ""

    def get(self, typ):
        """Kann überschrieben werden, um zusätzliche Parameter zu verarbeiten."""
        self.get_impl(typ)

    def get_impl(self, typ, additional_context=None):
        query = self.get_query()
        qmodel = compat.xdb_kind_from_query(query)
        kind = compat.xdb_kind(qmodel)
        if not self.filename:
            self.filename = u'%s-%s-%s' % (kind, datetime.datetime.now(), self.credential.uid)
        exporter = modelexporter.ModelExporter(
            qmodel, query, uid=self.credential.uid, **self.exporter_config)
        typ = typ.strip('/')
        if typ in {'xls', 'csv'}:
            self.check_download_permission()
            self.handle_download(typ, kind, exporter)
        else:
            loginfo = sui_models.sui_ExportLog.query(
                sui_models.sui_ExportLog.tablename == kind).order(
                -sui_models.sui_ExportLog.created_at).fetch_async(10)

            rowtemplate, headtemplate = self.get_rowtemplate(exporter)

            myvalues = self.default_template_vars({})

            values = dict(
                title=myvalues.get('title', self.title),
                widetable=self.widetable,
                filename=self.filename,
                downloadlog=loginfo.get_result(),
                header=self.header,
                rowtemplate=rowtemplate,
                headtemplate=headtemplate,
            )
            values.update(self.get_pagination(query))

            if additional_context:
                values.update(additional_context)

            if self.request.path.endswith('.html'):
                # needed to construct links
                values['listviewer_urlbase'] = self.request.path[:-5]
            self.render(values, self.template)

    def check_download_permission(self):
        """Prüft, ob der Nutzer die Tabelle downloaden darf."""

        if self.required_download_permission and not users.is_current_user_admin():
            for permission in self.required_download_permission:
                if self.has_permission(permission):
                    break
            else:
                if '@' in self.credential.uid:
                    bot.say("{} kann nicht downloaden: {} {}".format(
                        self.credential, self.request.url,
                        self.required_download_permission),
                        channel='#huwawi')
                raise gaetk.handler.HTTP403_Forbidden(
                    u'Sie benötigen eine der folgenden Berechtigungen: {}'.format(
                        u', '.join(self.required_download_permission)))

    def handle_download(self, typ, kind, exporter):
        """Query als Tabelle downloaden"""

        content_disposition = 'attachment; filename=%s' % self.filename.encode('ascii')
        if typ == 'xls':
            self.response.headers['Content-Type'] = 'application/msexcel'
            self.response.headers['content-disposition'] = content_disposition + '.xls'
            exporter.to_xls(self.response)
        elif typ == 'csv':
            self.response.headers['Content-Type'] = 'text/csv; charset=utf-8'
            self.response.headers['content-disposition'] = content_disposition + '.csv'
            exporter.to_csv(self.response)

        myvalues = self.default_template_vars({})
        log_export(
            kind,
            self.credential,
            self.request,
            self.response.headers['Content-Type'],
            myvalues.get('title', self.title))

    def get_rowtemplate(self, exporter):
        env = self.create_jinja2env()
        if self.fields:
            self.header = self.fields.keys()
            self.row = self.fields.values()
        rowstring = []
        if not self.row:
            self.row = [u'{{{{ object.{0}|e }}}}'.format(x) for x in exporter.fields]
            for field in exporter.fields:
                # Achtung, bei Verwendung von `str.format` muss "{" als "{{" geschrieben werden (escaping)
                rowstring.append(
                    u'<td class="autolink field_{0}">{{{{ object.{0}|e }}}}</td>'.format(field))
        else:
            for line in self.row:
                rowstring.append(u'<td class="autolink">{0}</td>'.format(line))
        rowstring = (u'<tr>%s</tr>' % ''.join(rowstring))
        rowtemplate = env.from_string(rowstring)
        if not self.header:
            self.header = exporter.fields
        headtemplate = env.from_string(self.get_headertext())
        return rowtemplate, headtemplate


def ListExportFactory(title, query, BaseClass=ListExportHandler, **kwargs):
    """Erzeugt eine Klasse mit den gewünschten Parametern."""

    classname = re.sub('[^A-Za-z0-9]+', '', title) + 'ListExport'
    if 'required_permission' not in kwargs:
        kwargs['required_permission'] = ['generic_permission']
    kwargs.update(title=title, query=query)
    return type(str(classname), (BaseClass,), kwargs,)


def log_export(tablename, credential, request, contenttype, title):
    """Protokolliere, dass jemand Daten exportiert."""
    if request.headers.get('User-Agent', '').startswith('resttest'):
        return   # ignore resttest

    sui_models.sui_ExportLog(
        tablename=tablename,
        title=title,
        uid=credential.uid,
        remote_addr=request.remote_addr,
        user_agent=request.headers.get('User-Agent', '?'),
        contenttype=contenttype
    ).put()
