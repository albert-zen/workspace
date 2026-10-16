# Validation

From the workspace repository root:

```text
python3 -m unittest discover -s practices/communication/html/review/tests -p "test_*.py"
```

The tests cover exact Git objects, normalized hashing, reflection bundles, single-file rendering, the problem-first review shell, explicit local restore controls, overview navigation hooks, successful application and stale-base rejection. They do not replace opening a real report and checking problem preview → complete review → problem overview, browser Back, every panel’s collapse → adjacent restore path, Zen mode, Markdown/source/diff switching, line and selection annotations, edit-triggered anchor invalidation, and responsive layout.
