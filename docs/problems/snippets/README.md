# Code as it was before each fix

The walkthrough videos for [docs/problems.md](../../problems.md) show the code before and after each fix.
When the "before" version is no longer anywhere in the tree, it is kept here, between `@learn:` markers,
so `scripts/build_problem_videos.py` can cut it out by anchor instead of anyone pasting code into JSON.
Nothing here is imported or run. (The failure-control paths `_reserve_unsafe` / `_give_back_unsafe` are still
in `stockhold/inventory.py`, because the tests switch them on.)
