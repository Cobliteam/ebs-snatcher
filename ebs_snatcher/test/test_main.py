import pytest

from .. import main

@pytest.mark.parametrize('value,result', [
    ('1', 1),
    ('0', ValueError),
    ('asd', ValueError),
    (None, TypeError)
])
def test_positive_int(value, result):
    if isinstance(result, type):
        with pytest.raises(result):
            main.positive_int(value)
    else:
        assert main.positive_int(value) == result


@pytest.mark.parametrize('value,result', [
    ('a=b', ('a', 'b')),
    ('a', ValueError),
    (None, TypeError)
])
def test_key_tag_pair(value, result):
    if isinstance(result, type):
        with pytest.raises(result):
            main.key_tag_pair(value)
    else:
        assert main.key_tag_pair(value) == result
