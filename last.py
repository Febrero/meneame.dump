#!/usr/bin/env python3

import signal
import sys
from queue import Queue
from threading import Thread

from core.api import Api
from core.db import DB
from core.threadme import ThreadMe
from core.util import read_yml_all, readlines, mkArg
import sys
import os

abspath = os.path.abspath(__file__)
dname = os.path.dirname(abspath)
os.chdir(dname)

arg = mkArg(
    "Obtiene las últimos links de meneame.net",
    cron="Muetra el mejor horario (en función de los datos ya obtenidos) para poner este script en cron",
    silent="No imprime trazas"
)
if arg.silent:
    print = lambda *args, **kargv: None

db = DB()
api = Api()

def update_info(id):
    print("%7d" % id, end="\r")
    r = api.get_link_info(id, fields=('clicks', 'comments', 'date', 'karma', 'negatives', 'sent_date', 'status', 'sub_status_id', 'user', 'votes'))
    return r

def get_user(user):
    r = api.get_list(sent_by=user)
    print("%4d %-20s" % (len(r), user), end="\r")
    return r

def get_user_cerrado(min_date):
    arr = db.to_list('''
        select distinct user_id, user
        from LINKS where
        sent_date<{0} and UNIX_TIMESTAMP(`check`)<{0}
    '''.format(min_date))
    users = set()
    visto = set()
    for id, user in arr:
        if id is not None:
            users.add(id)
            visto.add(user)
    for _, user in arr:
        if user not in visto:
            users.add(user)
    return users

def close_out(*args, **kargv):
    global db
    if db.closed:
        return
    ids = db.one("select count(id) from LINKS")
    print("\n"+str(ids), "links")
    db.close()

signal.signal(signal.SIGINT, lambda *args, **kargv: [close_out(), sys.exit(0)])

def main():
    print("Bucando enlaces...")
    links = api.get_links()
    print("Añadiendo user_id")
    links = api.fill_user_id(links)
    print(len(links), "links obtenidos")
    db.replace("LINKS", links)
    tm = ThreadMe(
        max_thread=30,
        list_size=2000
    )
    print("Actualizando enlaces cerrados...", end="\r")
    min_date = db.one("select max(sent_date) from LINKS")
    min_date = min_date - api.mnm_config['time_enabled_comments']
    print("Actualizando enlaces cerrados sent_date < %s" % min_date)
    cerrados = db.to_list('''
        select id
        from LINKS where
        sent_date<{0} and UNIX_TIMESTAMP(`check`)<{0}
    '''.format(min_date))
    update = "update `LINKS` set `check` = CURRENT_TIMESTAMP where id ";
    for links in tm.list_run(update_info, cerrados):
        links = api.fill_user_id(links)
        db.update("LINKS", links, skipNull=True)
        ids = set(str(i["id"]) for i in links)
        if len(ids)==1:
            ids = "= "+ids.pop()
        else:
            ids = "in (" + ", ".join(sorted(ids)) + ")"
        db.execute(update+ids)

def cron():
    print("Calculando horario para el cron...")
    print("Creando tabla auxiliar...")
    db.execute('''
        DROP TABLE IF EXISTS AUX_TABLE;
        create table AUX_TABLE (
           RN INT NOT NULL AUTO_INCREMENT,
           ID INT,
           DT INT,
           PRIMARY KEY (RN)
        );
        insert into AUX_TABLE (ID, DT)
        SELECT id, sent_date FROM LINKS
        where sent_date is not null
        order by sent_date;
    ''')
    db.commit()
    print("Obteniendo intervalo mínimo...")
    seconds = db.one('''
        select
            min(A.DT-B.DT) seconds
        from
            AUX_TABLE A join AUX_TABLE B on
            A.RN - 1000 = B.RN
    ''')
    print("Borrando tabla auxiliar...")
    db.execute("DROP TABLE AUX_TABLE;")
    db.commit()
    if seconds is not None:
        #seconds = seconds / 2
        h = int(seconds / (60*60))
        h = min(h, 12)
        print("15 */{0} * * * {1} --silent".format(h, os.path.realpath(__file__)))

if __name__ == "__main__":
    if arg.cron:
        cron()
        db.close()
        sys.exit()
    try:
        main()
    finally:
        close_out()