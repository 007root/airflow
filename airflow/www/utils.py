# -*- coding: utf-8 -*-
#
# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
#
# flake8: noqa: E402
import inspect
from future import standard_library
standard_library.install_aliases()  # noqa: E402
from builtins import str, object

from cgi import escape
from io import BytesIO as IO
import functools
import gzip
import io
import json
import os
import re
import time
import wtforms
from wtforms.compat import text_type
import zipfile

from flask import after_this_request, request, Markup, Response
from flask_admin.model import filters
import flask_admin.contrib.sqla.filters as sqlafilters
from flask_login import current_user
from six.moves.urllib.parse import urlencode

from airflow import configuration, models, settings
from airflow.utils.db import create_session
from airflow.utils import timezone
from airflow.utils.json import AirflowJsonEncoder

AUTHENTICATE = configuration.conf.getboolean('webserver', 'AUTHENTICATE')

DEFAULT_SENSITIVE_VARIABLE_FIELDS = (
    'password',
    'secret',
    'passwd',
    'authorization',
    'api_key',
    'apikey',
    'access_token',
)


def should_hide_value_for_key(key_name):
    # It is possible via importing variables from file that a key is empty.
    if key_name:
        config_set = configuration.conf.getboolean('admin',
                                                   'hide_sensitive_variable_fields')
        field_comp = any(s in key_name.lower() for s in DEFAULT_SENSITIVE_VARIABLE_FIELDS)
        return config_set and field_comp
    return False


class LoginMixin(object):
    def is_accessible(self):
        return (
            not AUTHENTICATE or (
                not current_user.is_anonymous and
                current_user.is_authenticated
            )
        )


class SuperUserMixin(object):
    def is_accessible(self):
        return (
            not AUTHENTICATE or
            (not current_user.is_anonymous and current_user.is_superuser())
        )


class DataProfilingMixin(object):
    def is_accessible(self):
        return (
            not AUTHENTICATE or
            (not current_user.is_anonymous and current_user.data_profiling())
        )


def get_params(**kwargs):
    if 'showPaused' in kwargs:
        v = kwargs['showPaused']
        if v or v is None:
            kwargs.pop('showPaused')
    return urlencode({d: v if v is not None else '' for d, v in kwargs.items()})


def generate_pages(current_page, num_of_pages,
                   search=None, showPaused=None, window=7):
    """
    Generates the HTML for a paging component using a similar logic to the paging
    auto-generated by Flask managed views. The paging component defines a number of
    pages visible in the pager (window) and once the user goes to a page beyond the
    largest visible, it would scroll to the right the page numbers and keeps the
    current one in the middle of the pager component. When in the last pages,
    the pages won't scroll and just keep moving until the last page. Pager also contains
    <first, previous, ..., next, last> pages.
    This component takes into account custom parameters such as search and showPaused,
    which could be added to the pages link in order to maintain the state between
    client and server. It also allows to make a bookmark on a specific paging state.
    :param current_page:
        the current page number, 0-indexed
    :param num_of_pages:
        the total number of pages
    :param search:
        the search query string, if any
    :param showPaused:
        false if paused dags will be hidden, otherwise true to show them
    :param window:
        the number of pages to be shown in the paging component (7 default)
    :return:
        the HTML string of the paging component
    """

    void_link = 'javascript:void(0)'
    first_node = Markup("""<li class="paginate_button {disabled}" id="dags_first">
    <a href="{href_link}" aria-controls="dags" data-dt-idx="0" tabindex="0">&laquo;</a>
</li>""")

    previous_node = Markup("""<li class="paginate_button previous {disabled}" id="dags_previous">
    <a href="{href_link}" aria-controls="dags" data-dt-idx="0" tabindex="0">&lt;</a>
</li>""")

    next_node = Markup("""<li class="paginate_button next {disabled}" id="dags_next">
    <a href="{href_link}" aria-controls="dags" data-dt-idx="3" tabindex="0">&gt;</a>
</li>""")

    last_node = Markup("""<li class="paginate_button {disabled}" id="dags_last">
    <a href="{href_link}" aria-controls="dags" data-dt-idx="3" tabindex="0">&raquo;</a>
</li>""")

    page_node = Markup("""<li class="paginate_button {is_active}">
    <a href="{href_link}" aria-controls="dags" data-dt-idx="2" tabindex="0">{page_num}</a>
</li>""")

    output = [Markup('<ul class="pagination" style="margin-top:0px;">')]

    is_disabled = 'disabled' if current_page <= 0 else ''
    output.append(first_node.format(href_link="?{}"
                                    .format(get_params(page=0,
                                                       search=search,
                                                       showPaused=showPaused)),
                                    disabled=is_disabled))

    page_link = void_link
    if current_page > 0:
        page_link = '?{}'.format(get_params(page=(current_page - 1),
                                            search=search,
                                            showPaused=showPaused))

    output.append(previous_node.format(href_link=page_link,
                                       disabled=is_disabled))

    mid = int(window / 2)
    last_page = num_of_pages - 1

    if current_page <= mid or num_of_pages < window:
        pages = [i for i in range(0, min(num_of_pages, window))]
    elif mid < current_page < last_page - mid:
        pages = [i for i in range(current_page - mid, current_page + mid + 1)]
    else:
        pages = [i for i in range(num_of_pages - window, last_page + 1)]

    def is_current(current, page):
        return page == current

    for page in pages:
        vals = {
            'is_active': 'active' if is_current(current_page, page) else '',
            'href_link': void_link if is_current(current_page, page)
                         else '?{}'.format(get_params(page=page,
                                                      search=search,
                                                      showPaused=showPaused)),
            'page_num': page + 1
        }
        output.append(page_node.format(**vals))

    is_disabled = 'disabled' if current_page >= num_of_pages - 1 else ''

    page_link = (void_link if current_page >= num_of_pages - 1
                 else '?{}'.format(get_params(page=current_page + 1,
                                              search=search,
                                              showPaused=showPaused)))

    output.append(next_node.format(href_link=page_link, disabled=is_disabled))
    output.append(last_node.format(href_link="?{}"
                                   .format(get_params(page=last_page,
                                                      search=search,
                                                      showPaused=showPaused)),
                                   disabled=is_disabled))

    output.append(Markup('</ul>'))

    return Markup('\n'.join(output))


def limit_sql(sql, limit, conn_type):
    sql = sql.strip()
    sql = sql.rstrip(';')
    if sql.lower().startswith("select"):
        if conn_type in ['mssql']:
            sql = """\
            SELECT TOP {limit} * FROM (
            {sql}
            ) qry
            """.format(limit=limit, sql=sql)
        elif conn_type in ['oracle']:
            sql = """\
            SELECT * FROM (
            {sql}
            ) qry
            WHERE ROWNUM <= {limit}
            """.format(limit=limit, sql=sql)
        else:
            sql = """\
            SELECT * FROM (
            {sql}
            ) qry
            LIMIT {limit}
            """.format(limit=limit, sql=sql)
    return sql


def epoch(dttm):
    """Returns an epoch-type date"""
    return int(time.mktime(dttm.timetuple())) * 1000,


def action_logging(f):
    """
    Decorator to log user actions
    """
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        # AnonymousUserMixin() has user attribute but its value is None.
        if current_user and hasattr(current_user, 'user') and current_user.user:
            user = current_user.user.username
        else:
            user = 'anonymous'

        log = models.Log(
            event=f.__name__,
            task_instance=None,
            owner=user,
            extra=str(list(request.args.items())),
            task_id=request.args.get('task_id'),
            dag_id=request.args.get('dag_id'))

        if request.args.get('execution_date'):
            log.execution_date = timezone.parse(request.args.get('execution_date'))

        with create_session() as session:
            session.add(log)
            session.commit()

        return f(*args, **kwargs)

    return wrapper


def notify_owner(f):
    """
    Decorator to notify owner of actions taken on their DAGs by others
    """
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        """
        if request.args.get('confirmed') == "true":
            dag_id = request.args.get('dag_id')
            task_id = request.args.get('task_id')
            dagbag = models.DagBag(settings.DAGS_FOLDER)
            dag = dagbag.get_dag(dag_id)
            task = dag.get_task(task_id)

            if current_user and hasattr(current_user, 'user') and current_user.user:
                user = current_user.username
            else:
                user = 'anonymous'

            if task.owner != user:
                subject = (
                    'Actions taken on DAG {0} by {1}'.format(
                        dag_id, user))
                items = request.args.items()
                content = Template('''
                    action: <i>{{ f.__name__ }}</i><br>
                    <br>
                    <b>Parameters</b>:<br>
                    <table>
                    {% for k, v in items %}
                        {% if k != 'origin' %}
                            <tr>
                                <td>{{ k }}</td>
                                <td>{{ v }}</td>
                            </tr>
                        {% endif %}
                    {% endfor %}
                    </table>
                    ''').render(**locals())
                if task.email:
                    send_email(task.email, subject, content)
        """
        return f(*args, **kwargs)
    return wrapper


def json_response(obj):
    """
    returns a json response from a json serializable python object
    """
    return Response(
        response=json.dumps(
            obj, indent=4, cls=AirflowJsonEncoder),
        status=200,
        mimetype="application/json")


def gzipped(f):
    """
    Decorator to make a view compressed
    """
    @functools.wraps(f)
    def view_func(*args, **kwargs):
        @after_this_request
        def zipper(response):
            accept_encoding = request.headers.get('Accept-Encoding', '')

            if 'gzip' not in accept_encoding.lower():
                return response

            response.direct_passthrough = False

            if (response.status_code < 200 or
                response.status_code >= 300 or
                'Content-Encoding' in response.headers):
                return response
            gzip_buffer = IO()
            gzip_file = gzip.GzipFile(mode='wb',
                                      fileobj=gzip_buffer)
            gzip_file.write(response.data)
            gzip_file.close()

            response.data = gzip_buffer.getvalue()
            response.headers['Content-Encoding'] = 'gzip'
            response.headers['Vary'] = 'Accept-Encoding'
            response.headers['Content-Length'] = len(response.data)

            return response

        return f(*args, **kwargs)

    return view_func


ZIP_REGEX = re.compile(r'((.*\.zip){})?(.*)'.format(re.escape(os.sep)))


def open_maybe_zipped(f, mode='r'):
    """
    Opens the given file. If the path contains a folder with a .zip suffix, then
    the folder is treated as a zip archive, opening the file inside the archive.

    :return: a file object, as in `open`, or as in `ZipFile.open`.
    """

    _, archive, filename = ZIP_REGEX.search(f).groups()
    if archive and zipfile.is_zipfile(archive):
        return zipfile.ZipFile(archive, mode=mode).open(filename)
    else:
        return io.open(f, mode=mode)


def make_cache_key(*args, **kwargs):
    """
    Used by cache to get a unique key per URL
    """
    path = request.path
    args = str(hash(frozenset(request.args.items())))
    return (path + args).encode('ascii', 'ignore')


def get_python_source(x):
    """
    Helper function to get Python source (or not), preventing exceptions
    """
    source_code = None

    if isinstance(x, functools.partial):
        source_code = inspect.getsource(x.func)

    if source_code is None:
        try:
            source_code = inspect.getsource(x)
        except TypeError:
            pass

    if source_code is None:
        try:
            source_code = inspect.getsource(x.__call__)
        except (TypeError, AttributeError):
            pass

    if source_code is None:
        source_code = 'No source code available for {}'.format(type(x))

    return source_code


class AceEditorWidget(wtforms.widgets.TextArea):
    """
    Renders an ACE code editor.
    """
    def __call__(self, field, **kwargs):
        kwargs.setdefault('id', field.id)
        html = '''
        <div id="{el_id}" style="height:100px;">{contents}</div>
        <textarea
            id="{el_id}_ace" name="{form_name}"
            style="display:none;visibility:hidden;">
        </textarea>
        '''.format(
            el_id=kwargs.get('id', field.id),
            contents=escape(text_type(field._value())),
            form_name=field.id,
        )
        return wtforms.widgets.core.HTMLString(html)


class UtcDateTimeFilterMixin(object):
    def clean(self, value):
        dt = super(UtcDateTimeFilterMixin, self).clean(value)
        return timezone.make_aware(dt, timezone=timezone.utc)


class UtcDateTimeEqualFilter(UtcDateTimeFilterMixin, sqlafilters.DateTimeEqualFilter):
    pass


class UtcDateTimeNotEqualFilter(UtcDateTimeFilterMixin, sqlafilters.DateTimeNotEqualFilter):
    pass


class UtcDateTimeGreaterFilter(UtcDateTimeFilterMixin, sqlafilters.DateTimeGreaterFilter):
    pass


class UtcDateTimeSmallerFilter(UtcDateTimeFilterMixin, sqlafilters.DateTimeSmallerFilter):
    pass


class UtcDateTimeBetweenFilter(UtcDateTimeFilterMixin, sqlafilters.DateTimeBetweenFilter):
    pass


class UtcDateTimeNotBetweenFilter(UtcDateTimeFilterMixin, sqlafilters.DateTimeNotBetweenFilter):
    pass


class UtcFilterConverter(sqlafilters.FilterConverter):

    utcdatetime_filters = (UtcDateTimeEqualFilter, UtcDateTimeNotEqualFilter,
                           UtcDateTimeGreaterFilter, UtcDateTimeSmallerFilter,
                           UtcDateTimeBetweenFilter, UtcDateTimeNotBetweenFilter,
                           sqlafilters.FilterEmpty)

    @filters.convert('utcdatetime')
    def conv_utcdatetime(self, column, name, **kwargs):
        return [f(column, name, **kwargs) for f in self.utcdatetime_filters]
