"""_missing_table must not treat column errors as missing capture tables."""
from app.routers.capture import _missing_table


def test_missing_table_true_for_undefined_relation():
    assert _missing_table(Exception('relation "capture_sessions" does not exist'))
    assert _missing_table(Exception("42P01: undefined_table"))
    assert _missing_table(
        Exception('Could not find the table \'public.capture_sessions\' in the schema cache')
    )


def test_missing_table_false_for_column_errors():
    assert not _missing_table(Exception('column "floor_plan_path" does not exist'))
    assert not _missing_table(Exception("PGRST204: Could not find the 'foo' column"))
    assert not _missing_table(Exception("permission denied for table capture_sessions"))
