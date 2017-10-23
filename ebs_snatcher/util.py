from __future__ import unicode_literals

from functools import wraps


def memoize(f):
    sentinel = object()

    @wraps(f)
    def memo(*args, **kwargs):
        if memo.value is sentinel:
            memo.value = f(*args, **kwargs)

        return memo.value

    memo.value = sentinel
    return memo
