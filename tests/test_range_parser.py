"""Tests for coverup.dialogs.common.parse_page_ranges.

Licensed under GPL-3.0
(c) 2024 - 2026 Björn Seipel
Acrobat-suite additions (c) 2026 CoverUP contributors
"""

import pytest

from coverup.dialogs.common import parse_page_ranges


def test_mixed_spec_from_contract():
    assert parse_page_ranges('1-3,7,9-', 12) == [0, 1, 2, 6, 8, 9, 10, 11]


def test_single_page():
    assert parse_page_ranges('5', 10) == [4]


def test_full_range():
    assert parse_page_ranges('1-10', 10) == list(range(10))


def test_open_ended_goes_to_last_page():
    assert parse_page_ranges('2-', 4) == [1, 2, 3]


def test_open_ended_on_last_page():
    assert parse_page_ranges('4-', 4) == [3]


def test_duplicates_and_overlaps_are_unique_and_sorted():
    assert parse_page_ranges('3,1,2-3,3', 5) == [0, 1, 2]


def test_whitespace_is_tolerated():
    assert parse_page_ranges(' 1 - 3 , 5 ', 6) == [0, 1, 2, 4]


def test_result_is_zero_based():
    assert parse_page_ranges('1', 1) == [0]


@pytest.mark.parametrize('spec', [
    'a', '0', '5-2', '1-99', '11', '-3', '1,,3', '1-2-3', '', '   ', ',',
    '9-',  # start beyond total (total=5 below)
])
def test_bad_tokens_raise_value_error(spec):
    with pytest.raises(ValueError, match='bad token'):
        parse_page_ranges(spec, 5)


def test_error_message_names_the_offending_token():
    with pytest.raises(ValueError) as excinfo:
        parse_page_ranges('1-3,zap,5', 10)
    assert 'bad token: zap' in str(excinfo.value)


def test_zero_page_document_rejects_everything():
    with pytest.raises(ValueError, match='bad token'):
        parse_page_ranges('1', 0)
