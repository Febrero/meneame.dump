import os
import re
import textwrap

import unidecode
import yaml
from bunch import Bunch
import MySQLdb
from .util import chunks
import sqlite3

import warnings
warnings.filterwarnings("ignore", category = MySQLdb.Warning)

re_sp = re.compile(r"\s+")

def parse_tag(_tag):
    tag = _tag
    for a, b in (
        ("á", "a"),
        ("é", "e"),
        ("í", "i"),
        ("ó", "o"),
        ("ú", "u")
    ):
        tag = tag.replace(a, b)
    if tag == "españa":
        return "España"
    if tag == "Europa":
        return "Europa"
    return _tag

def ResultIter(cursor, size=1000):
    while True:
        results = cursor.fetchmany(size)
        if not results:
            break
        for result in results:
            yield result

def save(file, content):
    if file and content:
        content = textwrap.dedent(content).strip()
        with open(file, "w") as f:
            f.write(content)

class DB:
    def __init__(self, debug_dir=None):
        self.tables = None
        self.closed = False
        self.insert_count = 0
        self.debug_dir = None
        if debug_dir and os.path.isdir(debug_dir):
            if not debug_dir.endswith("/"):
                debug_dir = debug_dir + "/"
            self.debug_dir = debug_dir
        self.name = 'meneame'
        host = os.environ.get("MARIADB_HOSTS", "localhost")
        port = os.environ.get("MARIADB_PORT", "3306")
        self.con = MySQLdb.connect(host=host, port=int(port), user='meneame', password='meneame', database=self.name)
        self.con.set_character_set('utf8mb4')
        cursor = self.con.cursor()
        cursor.execute('SET NAMES utf8mb4;')
        cursor.execute('SET CHARACTER SET utf8mb4;')
        cursor.execute('SET character_set_connection=utf8mb4;')
        cursor.close()
        self.load_tables()

    def load_tables(self):
        self.tables = dict()
        for t in self.table_names:
            try:
                self.tables[t] = self.get_cols("select * from "+t+" limit 0")
            except:
                pass

    @property
    def table_names(self):
        return tuple(i[0] for i in self.select("SELECT table_name FROM information_schema.tables where table_type in ('VIEW', 'BASE TABLE')"))

    def commit(self):
        self.con.commit()

    def get_cols(self, sql, cursor=None):
        if cursor is None:
            cursor = self.con.cursor()
        cursor.execute(sql)
        cols = tuple(col[0] for col in cursor.description)
        cursor.close()
        return cols

    def _build_select(self, sql):
        sql = sql.strip()
        if not sql.lower().startswith("select"):
            field = "*"
            if "." in sql:
                sql, field = sql.rsplit(".", 1)
            sql = "select "+field+" from "+sql
        return sql

    def select(self, sql, *args, cursor=None, **kargv):
        sql = self._build_select(sql)
        cursor = self.con.cursor(cursor)
        cursor.execute(sql)
        for r in ResultIter(cursor):
            yield r
        cursor.close()

    def one(self, sql):
        sql = self._build_select(sql)
        cursor = self.con.cursor()
        cursor.execute(sql)
        r = cursor.fetchone()
        cursor.close()
        if not isinstance(r, (list, tuple)):
            return r
        if not r:
            return None
        if len(r) == 1:
            return r[0]
        return r

    def parse_row(self, table, row):
        if row and not isinstance(row, dict):
            row = row[0]
        if not row:
            return None
        _cols = self.tables[table]
        cols=[]
        for k, v in sorted(row.items()):
            if k not in _cols:
                continue
            cols.append(k)
        return cols

    def insert(self, table, rows, insert="insert"):
        cols = self.parse_row(table, rows)
        if cols is None:
            return
        _cols = "`" + "`, `".join(cols) + "`"
        _vals = "%(" + ")s, %(".join(cols) + ")s"
        sql = insert+ " into `{0}` ({1}) values ({2})".format(table, _cols, _vals)
        vals = []
        cursor = self.con.cursor()
        cursor.executemany(sql, rows)
        cursor.close()
        self.con.commit()

    def replace(self, *args):
        self.insert(*args, insert="replace")

    def ignore(self, *args):
        self.insert(*args, insert="insert ignore")

    def update(self, table, rows):
        cols = self.parse_row(table, rows)
        if cols is None:
            return
        if "id" not in cols:
            raise Exception("id not found")
        cols.remove("id")
        sql_set = []
        for c in cols:
            sql_set.append("`{0}` = %({0})s".format(c))
        sql = "update `{0}` set " + ", ".join(sql_set)+" where id = %(id)s"
        cursor = self.con.cursor()
        cursor.executemany(sql, rows)
        cursor.close()
        self.con.commit()

    def to_list(self, *args, **kargv):
        lst = list(self.select(*args, **kargv))
        if lst and len(lst[0])==1:
            return [i[0] for i in lst]
        return lst

    def execute(self, sql):
        if os.path.isfile(sql):
            with open(sql, "r") as f:
                sql = f.read()
        sql = "\n".join(i for i in sql.split("\n") if i.strip()[:2] not in ("", "--"))
        cursor = self.con.cursor()
        for i in sql.split(";"):
            if i.strip():
                cursor.execute(i)
        cursor.close()
        self.con.commit()

    def close(self):
        if self.closed:
            return
        self.con.commit()
        self.con.close()
        self.closed = True

    def link_gaps(self, size=2000):
        max_id = self.one("select max(id) from LINKS")
        if max_id is not None:
            cursor = 1
            while cursor < max_id:
                ids = self.to_list('''
                    select distinct id from (
                        select id from LINKS
                        union
                        select id from broken_id where what='link'
                    ) T
                    where id>={0}
                    order by id
                    limit {1}
                '''.format(cursor, size))
                max_range = min(max_id, cursor+size+1)
                if len(ids) and max_range<=ids[-1]:
                    max_range=ids[-1]+1
                for i in range(cursor, max_range):
                    if i not in ids:
                        yield i
                cursor = max_range

    def comment_gaps(self, time_enabled_comments, size=2000):
        max_date = self.one("select max(sent_date) from LINKS")
        if max_date is not None:
            max_date = max_date - time_enabled_comments
            max_id = self.one("select max(id) from LINKS where sent_date<"+str(max_date))
            if max_id is not None:
                cursor = 1
                while cursor < max_id:
                    ids = self.to_list('''
                        select distinct id from (
                            select link id from COMMENTS
                            union
                            select id from broken_id where what in ('zero_comment', 'link')
                        ) T
                        where id>={0}
                        order by id
                        limit {1}
                    '''.format(cursor, size))
                    max_range = min(max_id, cursor+size+1)
                    if len(ids) and max_range<=ids[-1]:
                        max_range=ids[-1]+1
                    for i in range(cursor, max_range):
                        if i not in ids:
                            yield i
                    cursor = max_range

    def loop_tags(self, where=None):
        if where is not None:
            where = " and "+where
        else:
            where = ""
        for id, tags, status in db.select('''
            select
                id,
                LOWER(TRIM(tags)),
                status
            from LINKS
            where tags is not null and TRIM(tags)!='' {0}
        '''.format(where)):
            tags=[t.strip() for t in tags.split(",") if t.strip()]
            tags = [parse_tag(t) for t in set(tags)]
            tags = sorted(t for t in tags if t is not None)
            for tag in tags:
                yield {"link": id, "tag": tag, "status": status}

    def fix(self):
        self.execute("sql/update_users.sql")
        self.commit()
        self.execute("delete from TAGS;")
        self.commit()
        for tags_links in chunks(self.loop_tags(), 2000):
            db.insert("TAGS", tags_links)
        self.commit()
        self.execute('''
            delete from TAGS
            where tag not in (
                select tag from TAGS
                where status='published'
                group by tag
                having count(link)>=100
            )
        ''')
        self.commit()

    def clone(self, file, table):
        file = "file:"+file+"?mode=ro"
        lt = sqlite3.connect(file, detect_types=sqlite3.PARSE_DECLTYPES, uri=True)
        cols = self.get_cols("select * "+table+" limit 1", lt.cursor())
        cols = set(self.tables[table]).intersection(set(cols))
        cols = sorted(cols)
        _cols = "`" + "`, `".join(cols) + "`"
        _vals = ", ".join(["%s"] * len(cols))
        insert = "replace into `{0}` ({1}) values ({2})".format(table, _cols, _vals)

        _cols = '"' + '", "'.join(cols) + '"'
        select = "select {0} from {1}".format(_cols, table)
        if "id" in cols:
            select = select + " order by id"

        print(insert)
        print(select)
        return

        cursor = lt.cursor()
        cursor.execute(select)
        for rows in chunk(ResultIter(cursor), 1000):
            c = self.con.cursor()
            c.executemany(insert, rows)
            c.close()
            self.con.commit()
        cursor.close()
        lt.close()
