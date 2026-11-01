import os
import sys
import pytest



# Fixture credential for the TEST database only. It comes from the
# environment; the default is an obvious placeholder, so a copy of this
# file cannot authenticate anywhere. A hardcoded value in a test file
# is what put a live credential in a public repository once.
FIXTURE_PASSWORD = os.environ.get('MAIL_TEST_PASSWORD', 'not-a-real-password')

# ---------------------------------------------------------------------------
# Which database the suite runs against
#
# THE SUITE DELETES FROM THE TABLES IT USES. The `db` fixture empties
# emails, attachments, users, folders, domains, outbound_queue and friends
# before every test, so it must only ever be pointed at a database that is
# disposable.
#
# There used to be a default:
#
#     postgresql://postgres:<pw>@localhost:5432/mail_server_test
#
# and a database with that EXACT NAME EXISTS ON THE LIVE PRODUCTION
# POSTGRES on port 5432. So `POSTGRES_PASSWORD_TEST=<pw> pytest` could
# delete from a live database, and the only thing standing between that and
# a wipe was the operator noticing the port.
#
# The URL the suite runs against. MAIL_TEST_DATABASE_URL wins; otherwise
# the old POSTGRES_PASSWORD_TEST form is used, and the guards below decide
# whether the result is a safe place to run.
#
# To run the tests against a throwaway cluster:
#
#   MAIL_TEST_DATABASE_URL='postgresql://postgres@127.0.0.1:55432/mail_ci_test' pytest tests/
#
# POSTGRES_PASSWORD_TEST is still accepted, and supplies the password for
# the URL when the URL does not carry one.
# ---------------------------------------------------------------------------

def _apply_password(url):
    """Let POSTGRES_PASSWORD_TEST supply the password for the URL."""
    pw = os.environ.get('POSTGRES_PASSWORD_TEST')
    if not pw:
        return url
    scheme, sep, rest = url.partition('://')
    if not sep or '@' not in rest:
        return url
    userinfo, _, hostpart = rest.rpartition('@')
    user, colon, _old = userinfo.partition(':')
    if colon:
        return url
    return '%s://%s:%s@%s' % (scheme, user, pw, hostpart)


def _default_url():
    """The URL used when MAIL_TEST_DATABASE_URL is not set.

    This is the old default, kept so a runner that sets only
    POSTGRES_PASSWORD_TEST keeps working. It is NOT trusted: the guards
    below still have to pass before anything is deleted.
    """
    return 'postgresql://postgres:%s@localhost:5432/mail_server_test' % (
        os.environ.get('POSTGRES_PASSWORD_TEST', ''),
    )


_test_db_url = _apply_password(
    os.environ.get('MAIL_TEST_DATABASE_URL') or _default_url()
)


def _refuse(message):
    sys.stderr.write(
        '\n'
        '====================================================================\n'
        '  %s\n'
        '====================================================================\n'
        '\n' % message
    )
    sys.exit(2)


def _assert_disposable(url):
    """Refuse to run against anything that is not a throwaway test database.

    Two things are checked:

      1. the database name must contain "test";
      2. the PostgreSQL server must not also host the production
         `mail_server` database -- that is the live server, whatever the
         database is called. This is the check that matters: a database
         named `mail_server_test` EXISTS on the live production Postgres,
         and the suite's `db` fixture DELETEs from it.
    """
    from urllib.parse import urlparse
    dbname = (urlparse(url).path or '').lstrip('/')
    if 'test' not in dbname.lower():
        _refuse(
            'Refusing to run: the database name %r does not contain "test".\n'
            '  This suite DELETEs from the tables it uses.' % dbname
        )

    import psycopg2
    try:
        conn = psycopg2.connect(url)
    except Exception as exc:
        _refuse('Cannot reach the database in MAIL_TEST_DATABASE_URL: %s' % exc)
    try:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM pg_database WHERE datname = 'mail_server'")
        if cur.fetchone():
            _refuse(
                'Refusing to run: that PostgreSQL server also hosts the\n'
                '  production `mail_server` database, so it is the live server.\n'
                '  This suite DELETEs from the tables it uses.'
            )
    finally:
        conn.close()


def _harden_the_test_database(url):
    """Stop a lock wait from hanging the run.

    A test session that leaves a transaction open holds locks on the
    tables, and the schema helpers' `ALTER TABLE emails ...` then waits for
    them. A lock wait at the C level cannot be interrupted by pytest's
    timeout, so the run hung until the job was cancelled and the report
    was lost.

    lock_timeout makes a blocked statement fail fast instead of waiting
    forever, and idle_in_transaction_session_timeout closes a session that
    is sitting on a transaction it is not using. Between them the suite
    finishes in well under two minutes and reports what actually failed.

    Both are set on the DATABASE, so every connection to it gets them --
    including the ones the application opens. This needs superuser; if the
    role is not one, the suite still runs, it just hangs again if something
    leaks a transaction.
    """
    import psycopg2
    from psycopg2 import sql

    conn = psycopg2.connect(url)
    try:
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute('SELECT current_database()')
        dbname = cur.fetchone()[0]
        for setting, value in (
            ('lock_timeout', '10s'),
            ('idle_in_transaction_session_timeout', '30s'),
        ):
            cur.execute(
                sql.SQL('ALTER DATABASE {} SET {} = %s').format(
                    sql.Identifier(dbname), sql.SQL(setting)
                ),
                (value,),
            )
    except Exception as exc:
        sys.stderr.write(
            '\n'
            '  WARNING: could not set lock_timeout on the test database: %s\n'
            '  The suite may hang if a test leaves a transaction open.\n'
            '\n' % exc
        )
    finally:
        conn.close()


_assert_disposable(_test_db_url)
_harden_the_test_database(_test_db_url)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ['TESTING'] = 'true'
os.environ['DATABASE_URL'] = _test_db_url

os.environ['JWT_SECRET'] = 'test-secret-key'
os.environ['SMTP2GO_WEBHOOK_SECRET'] = ''

from app import create_app
from app.db import get_db_connection, seed_local_domains


@pytest.fixture
def app():
	app = create_app()
	app.config['TESTING'] = True
	app.config['JWT_SECRET'] = 'test-secret-key'
	return app


@pytest.fixture
def client(app):
	return app.test_client()


@pytest.fixture
def db():
	conn = get_db_connection()
	cursor = conn.cursor()

	# Clean up test data but keep users for auth
	cursor.execute("DELETE FROM delivery_logs")
	cursor.execute("DELETE FROM outbound_queue")
	cursor.execute("DELETE FROM email_recipients")
	cursor.execute("DELETE FROM attachments")
	cursor.execute("DELETE FROM emails")
	cursor.execute("DELETE FROM folders")
	cursor.execute("DELETE FROM domains")
	cursor.execute("DELETE FROM ip_blacklist")
	# Note: We don't delete users here because auth_headers fixture needs them

	conn.commit()
	seed_local_domains()
	cursor.close()
	conn.close()

	return get_db_connection


@pytest.fixture
def auth_headers(client, db):
	"""Create a test user and return auth headers"""
	# First clean up any existing test user
	conn = get_db_connection()
	cursor = conn.cursor()
	cursor.execute("DELETE FROM users WHERE email = 'test@example.com'")
	conn.commit()
	cursor.close()
	conn.close()

	# Register new user
	response = client.post('/auth/register', json={
		'email': 'test@example.com',
		'password': FIXTURE_PASSWORD,
		'name': 'Test User'
	})

	# Login to get token
	response = client.post('/auth/login', json={
		'email': 'test@example.com',
		'password': FIXTURE_PASSWORD
	})

	data = response.get_json()
	token = data['token']
	return {'Authorization': f'Bearer {token}'}


@pytest.fixture
def auth_headers_second_user(client, db):
	"""Create a second test user and return auth headers for cross-user security tests"""
	# First clean up any existing test user
	conn = get_db_connection()
	cursor = conn.cursor()
	cursor.execute("DELETE FROM users WHERE email = 'test2@example.com'")
	conn.commit()
	cursor.close()
	conn.close()

	# Register new user
	response = client.post('/auth/register', json={
		'email': 'test2@example.com',
		'password': 'testpassword456',
		'name': 'Test User 2'
	})

	# Login to get token
	response = client.post('/auth/login', json={
		'email': 'test2@example.com',
		'password': 'testpassword456'
	})

	data = response.get_json()
	token = data['token']
	return {'Authorization': f'Bearer {token}'}