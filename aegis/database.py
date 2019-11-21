#-*- coding: utf-8 -*-
#
# Fork of Tornado Database using Postgres and Mysql


# Python Imports
import logging
import threading
import time

# Extern Imports
import tornado.options
from tornado.options import options
import aegis.stdlib

# Import drivers as needed and set up error classes
pgsql_available = False
pgsql_IntegrityError = None
pgsql_OperationalError = None
pgsql_DatabaseError = None
try:
    import psycopg2
    pgsql_available = True
    # These are here for mapping errors from psycopg2 into application namespace
    pgsql_IntegrityError = psycopg2.IntegrityError
    pgsql_OperationalError = psycopg2.OperationalError
    pgsql_DatabaseError = psycopg2.Error
except Exception as ex:
    logging.error("Couldn't import psycopg2 - maybe that's ok for now.")

mysql_available = False
mysql_IntegrityError = None
mysql_OperationalError = None
mysql_DataError = None
try:
    import MySQLdb
    mysql_available = True
    # These are here for mapping errors from MySQLdb into application namespace
    from MySQLdb._exceptions import IntegrityError as mysqldb_IntegrityError
    from MySQLdb._exceptions import OperationalError as mysqldb_OperationalError
    from MySQLdb._exceptions import DataError as mysqldb_DataError
    mysql_IntegrityError = mysqldb_IntegrityError
    mysql_OperationalError = mysqldb_OperationalError
    mysql_DataError = mysqldb_DataError
except Exception as ex:
    logging.error("Couldn't import MySQLdb - maybe that's ok for now.")


# Thread-safe persistent database connection
dbconns = threading.local()


def db(use_schema=None):
    if not hasattr(dbconns, 'databases'):
        dbconns.databases = {}
    if pgsql_available and options.pg_database not in dbconns.databases:
        dbconns.databases[options.pg_database] = PostgresConnection.connect()
        use_schema = options.pg_database
    if mysql_available and options.mysql_schema not in dbconns.databases:
        dbconns.databases[options.mysql_schema] = MysqlConnection.connect()
        use_schema = options.mysql_schema
    # Default situation - much better to be explicit which database we're connecting to!
    if not use_schema and len(dbconns.databases) == 1:
        use_schema = [dbconn for dbconn in dbconns.databases.keys()][0]
    return dbconns.databases[use_schema]


def dbnow(use_schema=None):
    return db(use_schema).get("SELECT NOW()")


class PostgresConnection(object):
    threads = {}

    def __init__(self, hostname, port, database, username=None, password=None, max_idle_time=7 * 3600):
        self.hostname = hostname
        self.port = port
        self.database = database
        self.max_idle_time = max_idle_time
        args = "port={0} dbname={1}".format(self.port, self.database)
        if hostname is not None:
            args += " host={0}".format(hostname)
        if username is not None:
            args += " user={0}".format(username)
        if password is not None:
            args += " password={0}".format(password)
        self._db = None
        self._db_args = args
        self._last_use_time = time.time()
        try:
            self.reconnect()
        except Exception:
            logging.error("Cannot connect to PostgreSQL: %s", self.hostname, exc_info=True)

    def __del__(self):
        self.close()

    def close(self):
        if getattr(self, "_db", None) is not None:
            self._db.close()
            self._db = None

    @classmethod
    def connect(cls, **kwargs):
        if 'pg_database' in kwargs:
            database = kwargs['pg_database']
            hostname = kwargs['pg_hostname']
            username = kwargs['pg_username']
            password = kwargs['pg_password']
            port = kwargs.get('pg_port', 5432)
        else:
            database = options.pg_database
            hostname = options.pg_hostname
            username = options.pg_username
            password = options.pg_password
            port = options.pg_port
        # force a new connection
        if kwargs.get('force', False):
            return cls(hostname, port, database, username, password)
        # check existing connections
        ident = threading.current_thread().ident
        connections = cls.threads.setdefault(ident, {})
        if not database in connections:
            conn = cls(hostname, port, database, username, password)
            conn.database = database
            cls.threads[ident][database] = conn
        return connections[database]

    def reconnect(self):
        self.close()
        self._db = psycopg2.connect(self._db_args)

    def query(self, query, *parameters, **kwargs):
        """ Returns a row list for the given query and parameters."""
        cursor = self._cursor()
        try:
            self._execute(cursor, query, parameters)
            column_names = [d[0] for d in cursor.description]
            cls = kwargs.get('cls')
            if cls:
                rows = [cls(list(zip(column_names, row))) for row in cursor]
                return rows
            else:
                return [Row(zip(column_names, row)) for row in cursor]
        finally:
            cursor.close()

    def get(self, query, *parameters, **kwargs):
        """ Returns the first row returned for the given query."""
        rows = self.query(query, *parameters, **kwargs)
        if not rows:
            return None
        elif len(rows) > 1:
            raise Exception("Multiple rows returned for Database.get() query")
        else:
            return rows[0]

    def execute(self, query, *parameters):
        if query.startswith('INSERT'):
            return self.execute_lastrowid(query, *parameters)
        else:
            return self.execute_rowcount(query, *parameters)

    def execute_lastrowid(self, query, *parameters):
        """ Executes the given query, returning the lastrowid from the query."""
        cursor = self._cursor()
        try:
            self._execute(cursor, query, parameters)
            if cursor.rowcount > 0:
                last_row_id = cursor.fetchone()[0]
                #aegis.stdlib.logw(last_row_id, "LAST ROW ID")
                return last_row_id
        finally:
            cursor.close()

    def execute_rowcount(self, query, *parameters):
        """ Return the rowcount from the query."""
        cursor = self._cursor()
        try:
            self._execute(cursor, query, parameters)
            return cursor.rowcount
        finally:
            cursor.close()

    def executemany(self, query, parameters):
        """ Return the lastrowid from the query."""
        return self.executemany_lastrowid(query, parameters)

    def executemany_lastrowid(self, query, parameters):
        """ Return the lastrowid from the query."""
        cursor = self._cursor()
        try:
            cursor.executemany(query, parameters)
            return cursor.lastrowid
        finally:
            cursor.close()

    def executemany_rowcount(self, query, parameters):
        """ Return the rowcount from the query."""
        cursor = self._cursor()
        try:
            cursor.executemany(query, parameters)
            return cursor.rowcount
        finally:
            cursor.close()

    def _ensure_connected(self):
        """ If connection is open for more than max_idle_time, close and reconnect """
        if (self._db is None or (time.time() - self._last_use_time > self.max_idle_time)):
            self.reconnect()
        self._last_use_time = time.time()

    def _cursor(self):
        self._ensure_connected()
        return self._db.cursor()

    def _execute(self, cursor, query, parameters):
        try:
            # return cursor.execute(query, parameters)
            cursor.execute(query, parameters)
            return self._db.commit()
        except pgsql_OperationalError:
            logging.error("Error connecting to PostgreSQL")
            self.close()
            raise
        except pgsql_DatabaseError:
            logging.error("General Error at PostgreSQL - rollback transaction and carry on!")
            self.rollback()
            raise

    def rollback(self):
        if getattr(self, "_db", None) is not None:
            self._db.rollback()


class MysqlConnection(object):
    """ From torndb originally """
    def __init__(self, host, database, user=None, password=None, max_idle_time=7 * 3600):
        self.host = host
        self.database = database
        self.max_idle_time = max_idle_time
        args = dict(use_unicode=True, charset="utf8mb4", db=database, sql_mode="TRADITIONAL")
        if user is not None:
            args["user"] = user
        if password is not None:
            args["passwd"] = password
        # We accept a path to a MySQL socket file or a host(:port) string
        if "/" in host:
            args["unix_socket"] = host
        else:
            self.socket = None
            pair = host.split(":")
            if len(pair) == 2:
                args["host"] = pair[0]
                args["port"] = int(pair[1])
            else:
                args["host"] = host
                args["port"] = 3306
        self._db_init_command = 'SET time_zone = "+0:00"'
        self._db = None
        self._db_args = args
        self._last_use_time = time.time()
        try:
            self.reconnect()
        except Exception:
            logging.error("Cannot connect to MySQL on %s", self.host, exc_info=True)

    threads = {}

    @classmethod
    def connect(cls, **kwargs):
        if 'mysql_schema' in kwargs:
            host = kwargs['mysql_host']
            schema = kwargs['mysql_schema']
            user = kwargs['mysql_user']
            passwd = kwargs['mysql_password']
        else:
            host = options.mysql_host
            schema = options.mysql_schema
            user = options.mysql_user
            passwd = options.mysql_password
        # force a new connection
        if kwargs.get('force', False):
            return cls(host, schema, user, passwd)
        # check existing connections
        ident = threading.current_thread().ident
        target = '%s@%s' % (schema, host)
        connections = cls.threads.setdefault(ident, {})
        if not target in connections:
            conn = cls(host, schema, user, passwd)
            conn.execute("SET NAMES utf8mb4 COLLATE utf8mb4_unicode_ci", disable_audit_sql=True)
            conn.schema = schema
            cls.threads[ident][target] = conn
        return connections[target]

    def __del__(self):
        self.close()

    def close(self):
        """Closes this database connection."""
        if getattr(self, "_db", None) is not None:
            self._db.close()
            self._db = None

    def reconnect(self):
        """Closes the existing database connection and re-opens it."""
        self.close()
        self._db = MySQLdb.connect(autocommit=True, **self._db_args)
        self.execute(self._db_init_command, disable_audit_sql=True)

    def iter(self, query, *parameters, **kwargs):
        """Returns an iterator for the given query and parameters."""
        self._ensure_connected()
        cursor = self._db.cursor(SSCursor)
        try:
            self._execute(cursor, query, parameters)
            column_names = [d[0] for d in cursor.description]
            if kwargs.get('cls'):
                for row in cursor:
                    yield kwargs['cls'](zip(column_names, row))
            else:
                for row in cursor:
                    yield Row(zip(column_names, row))
        finally:
            cursor.close()

    def query(self, query, *parameters, **kwargs):
        """Returns a row list for the given query and parameters."""
        cursor = self._cursor()
        try:
            self._execute(cursor, query, parameters)
            column_names = [d[0] for d in cursor.description]
            if kwargs.get('cls'):
                return [kwargs['cls'](zip(column_names, row)) for row in cursor]
            else:
                return [Row(zip(column_names, row)) for row in cursor]
        finally:
            cursor.close()

    def get(self, query, *parameters, **kwargs):
        """Returns the first row returned for the given query."""
        rows = self.query(query, *parameters)
        if not rows:
            return None
        elif len(rows) > 1:
            raise Exception("Multiple rows returned for Database.get() query")
        else:
            row = rows[0]
            if row and kwargs.get('cls'):
                row = kwargs['cls'](row)
            return row

    # rowcount is a more reasonable default return value than lastrowid,
    # but for historical compatibility execute() must return lastrowid.
    def execute(self, query, *parameters, **kwargs):
        """Executes the given query, returning the lastrowid from the query."""
        return self.execute_lastrowid(query, *parameters)

    def execute_lastrowid(self, query, *parameters):
        """Executes the given query, returning the lastrowid from the query."""
        cursor = self._cursor()
        try:
            self._execute(cursor, query, parameters)
            return cursor.lastrowid
        finally:
            cursor.close()

    def execute_rowcount(self, query, *parameters):
        """Executes the given query, returning the rowcount from the query."""
        cursor = self._cursor()
        try:
            self._execute(cursor, query, parameters)
            return cursor.rowcount
        finally:
            cursor.close()

    def executemany(self, query, parameters):
        """Executes the given query against all the given param sequences.
        We return the lastrowid from the query.
        """
        return self.executemany_lastrowid(query, parameters)

    def executemany_lastrowid(self, query, parameters):
        """Executes the given query against all the given param sequences.
        We return the lastrowid from the query.
        """
        cursor = self._cursor()
        try:
            cursor.executemany(query, parameters)
            return cursor.lastrowid
        finally:
            cursor.close()

    def executemany_rowcount(self, query, parameters):
        """Executes the given query against all the given param sequences.
        We return the rowcount from the query.
        """
        cursor = self._cursor()
        try:
            cursor.executemany(query, parameters)
            return cursor.rowcount
        finally:
            cursor.close()

    def _ensure_connected(self):
        # Mysql by default closes client connections that are idle for
        # 8 hours, but the client library does not report this fact until
        # you try to perform a query and it fails.  Protect against this
        # case by preemptively closing and reopening the connection
        # if it has been idle for too long (7 hours by default).
        if (self._db is None or
                (time.time() - self._last_use_time > self.max_idle_time)):
            self._last_use_time = time.time()
            self.reconnect()

    def _cursor(self):
        self._ensure_connected()
        return self._db.cursor()

    def _execute(self, cursor, query, parameters):
        try:
            return cursor.execute(query, parameters)
        except mysql_OperationalError:
            logging.error("Error connecting to MySQL on %s", self.host)
            self.close()
            raise


# To support inserting something literally, like NOW(), into mini-ORM below
class Literal(str):
    pass


class Row(dict):
    """ A dict that allows for object-like property access syntax."""
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)

    @classmethod
    def logw(cls, msg, value, row_id):
        logging.warning("%s: %s %s", msg, value, row_id)

    @classmethod
    def scan_id(cls, column, row_id):
        sql = 'SELECT * FROM %s WHERE %s=%%s' % (cls.table_name, column)
        return db().query(sql, row_id, cls=cls)

    @classmethod
    def map_items(cls, items, key):
        return cls([(item[key], item) for item in items])

    @classmethod
    def map_id(cls, row_id, where_col, key_col, debug=False):
        items = cls.map_items(cls.scan_id(where_col, row_id), key_col)
        if debug:
            cls.logw("WHERE", where_col, row_id)
            logging.warning("")
            cls.logw("SCAN", cls.scan_id(where_col, row_id), row_id)
            logging.warning("")
            cls.logw("ITEMS", items, row_id)
        return items

    @classmethod
    def get_id(cls, column_id_val, member_id=None):
        if not column_id_val:
            return None
        sql = 'SELECT * FROM %s WHERE %s=%%s'
        args = [int(column_id_val)]
        if member_id:
            sql = sql + ' AND member_id=%%s'
            args.append(int(member_id))
        sql = sql % (cls.table_name, cls.id_column)
        val = db().get(sql, *args, cls=cls)
        return val

    # kva_split(), insert(), update() together are a mini-ORM in processing arbitrary column-value combinations on a row.
    # define table_name and data_columns to know which are allowed to be set along with user action
    # columns and where are simple dictionaries: {'full_name': "FULL NAME", 'email': 'email@example.com'}
    @staticmethod
    def kva_split(columns):
        keys = []
        values = []
        args = []
        for key, value in columns.items():
            keys.append('%s' % key)
            if isinstance(value, Literal):
                values.append(value)
            else:
                values.append('%s')
                args.append(value)
        return keys, values, args

    @classmethod
    def insert_columns(cls, sql_txt='INSERT INTO %(db_table)s (%(keys)s) VALUES (%(values)s)', **columns):
        db_table = cls.table_name
        keys, values, args = cls.kva_split(columns)
        use_db = db()
        aegis.stdlib.logw(use_db, "USE DB")
        if type(use_db) is PostgresConnection:
            sql_txt += " RETURNING " + cls.id_column
        sql = sql_txt % {'db_table': db_table, 'keys': ', '.join(keys), 'values': ', '.join(values)}
        return use_db.execute(sql, *args)

    @classmethod
    def update_columns(cls, columns, where):
        if not columns:
            logging.debug('Nothing to update. Skipping query')
            return
        db_table = cls.table_name
        # SET clause
        keys, values, args = cls.kva_split(columns)
        set_clause = ', '.join(['%s=%s' % (key, value) for key, value in zip(keys, values)])
        # WHERE clause
        keys, values, args2 = cls.kva_split(where)
        args += args2
        where_clause = ' AND '.join(['%s=%s' % (key, value) for key, value in zip(keys, values)])
        # SQL statement
        sql = 'UPDATE %s SET %s WHERE %s' % (db_table, set_clause, where_clause)
        return db().execute_rowcount(sql, *args)


class SqlDiff(Row):
    table_name = 'sql_diff'
    id_column = 'sql_diff_id'

    @staticmethod
    def create_table():
        sql_diff_table = """
            CREATE TABLE IF NOT EXISTS
            sql_diff (
              sql_diff_id SERIAL NOT NULL,
              sql_diff_name VARCHAR(80) NOT NULL,
              create_dttm TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
              applied_dttm TIMESTAMP DEFAULT NULL,
              PRIMARY KEY (sql_diff_name)
            )"""
        return db().execute(sql_diff_table)

    @staticmethod
    def insert(sql_diff_name):
        sql = 'INSERT INTO sql_diff (sql_diff_name) VALUES (%s) RETURNING sql_diff_id'
        return db().execute(sql, sql_diff_name)

    @classmethod
    def scan(cls):
        sql = 'SELECT * FROM sql_diff'
        return db().query(sql, cls=cls)

    @staticmethod
    def mark_applied(sql_diff_name):
        sql = 'UPDATE sql_diff SET applied_dttm=NOW() WHERE sql_diff_name=%s'
        return db().execute(sql, sql_diff_name)

    @classmethod
    def scan_unapplied(cls):
        sql = """SELECT * FROM sql_diff WHERE applied_dttm IS NULL ORDER BY SUBSTRING(sql_diff_name from 5 for 3) ASC"""
        return db().query(sql, cls=cls)
