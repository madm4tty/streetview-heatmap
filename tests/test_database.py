import database


def test_close_db(tmp_path):
    path = tmp_path / 'test.db'
    database.init_db(str(path))
    assert database._conn is not None
    database.close_db()
    assert database._conn is None
    # Call again without prior init to ensure no exception
    database.close_db()
