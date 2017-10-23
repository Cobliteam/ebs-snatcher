from functools import wraps

def memoize(f):
    sentinel = object()
    value = sentinel

    @wraps(f)
    def memoized(*args, **kwargs):
        nonlocal value
        if value is sentinel:
            value = f(*args, **kwargs)

        return value

    return memoized
