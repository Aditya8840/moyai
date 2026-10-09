"""Handcrafted output mutations, not model runs or estimated regression recall."""
from dataclasses import dataclass
from textwrap import dedent


def source(value):
    return dedent(value).lstrip()


BASELINES = {
    'stable-deduplication': source('''
        def stable_unique(values):
            return list(dict.fromkeys(values))
    '''),
    'merge-intervals': source('''
        def merge_intervals(intervals):
            result = []
            for start, end in sorted(intervals):
                if result and start <= result[-1][1]:
                    result[-1][1] = max(end, result[-1][1])
                else:
                    result.append([start, end])
            return result
    '''),
    'iterable-chunking': source('''
        def chunked(items, size):
            if size <= 0:
                raise ValueError()
            items = list(items)
            return [items[i:i + size] for i in range(0, len(items), size)]
    '''),
    'strict-boolean-parsing': source('''
        def parse_bool(value):
            values = {'true': True, 'yes': True, '1': True,
                      'false': False, 'no': False, '0': False}
            try:
                return values[value.strip().lower()]
            except KeyError:
                raise ValueError() from None
    '''),
}


EXTRA_CHECKS = {
    'stable-deduplication': source('''
        assert f([1, '1', 1, (1,), '1']) == [1, '1', (1,)]
        assert f([None, 'None', None]) == [None, 'None']
    '''),
    'merge-intervals': source('''
        assert f([[1, 1]]) == [[1, 1]]
        assert f([[3, 3], [1, 2]]) == [[1, 2], [3, 3]]
        assert f([[-5, -3], [-4, -1]]) == [[-5, -1]]
    '''),
    'iterable-chunking': source('''
        assert f([1, 2, 3], 1) == [[1], [2], [3]]
        assert f([1, 2, 3, 4], 3) == [[1, 2, 3], [4]]
        assert f((i for i in range(3)), 8) == [[0, 1, 2]]
    '''),
    'strict-boolean-parsing': source('''
        for value in ['+1', '-0', '1.0', '0.0', 'truex', 'false extra']:
            try:
                f(value)
            except ValueError:
                pass
            else:
                raise AssertionError('noncanonical boolean token must be rejected')
    '''),
}


@dataclass(frozen=True)
class Mutation:
    id: str
    case: str
    defect: str
    solution: str


MUTATIONS = [
    Mutation('sort-deduplication', 'stable-deduplication',
             'Sorts unique values instead of preserving their first occurrence',
             'def stable_unique(values):\n    return sorted(set(values))\n'),
    Mutation('mutate-deduplication-input', 'stable-deduplication',
             'Replaces the caller list with deduplicated values',
             'def stable_unique(values):\n    values[:] = dict.fromkeys(values)\n    return values\n'),
    Mutation('stringify-deduplication-keys', 'stable-deduplication',
             'Conflates distinct hashable values with the same string representation',
             source('''
                 def stable_unique(values):
                     seen, result = set(), []
                     for value in values:
                         key = str(value)
                         if key not in seen:
                             seen.add(key)
                             result.append(value)
                     return result
             ''')),
    Mutation('exclude-touching-intervals', 'merge-intervals',
             'Does not merge touching endpoints',
             BASELINES['merge-intervals'].replace('start <=', 'start <')),
    Mutation('sort-intervals-in-place', 'merge-intervals',
             'Mutates the caller list while sorting',
             BASELINES['merge-intervals'].replace('    result = []',
                                                 '    intervals.sort()\n    result = []')),
    Mutation('drop-zero-width-intervals', 'merge-intervals',
             'Discards valid zero-width intervals',
             BASELINES['merge-intervals'].replace('        if result',
                                                 '        if start == end:\n            continue\n        if result')),
    Mutation('slice-generator', 'iterable-chunking',
             'Assumes all iterables support length and slicing',
             BASELINES['iterable-chunking'].replace('    items = list(items)\n', '')),
    Mutation('drop-remainder-chunk', 'iterable-chunking',
             'Discards the final shorter chunk',
             BASELINES['iterable-chunking'].replace('range(0, len(items), size)',
                                                    'range(0, len(items) - size + 1, size)')),
    Mutation('size-one-shortcut', 'iterable-chunking',
             'Returns the flat input for chunk size one',
             BASELINES['iterable-chunking'].replace('    return [items',
                                                    '    if size == 1:\n        return items\n    return [items')),
    Mutation('skip-whitespace-normalization', 'strict-boolean-parsing',
             'Rejects accepted tokens surrounded by whitespace',
             BASELINES['strict-boolean-parsing'].replace('.strip()', '')),
    Mutation('python-truthiness', 'strict-boolean-parsing',
             'Treats every nonempty string, including false, as true',
             'def parse_bool(value):\n    return bool(value)\n'),
    Mutation('accept-numeric-aliases', 'strict-boolean-parsing',
             'Accepts unspecified numeric strings such as +1 and 1.0',
             source('''
                 def parse_bool(value):
                     value = value.strip().lower()
                     if value in {'true', 'yes'}:
                         return True
                     if value in {'false', 'no'}:
                         return False
                     numeric = float(value)
                     if numeric not in (0, 1):
                         raise ValueError()
                     return bool(numeric)
             ''')),
]
