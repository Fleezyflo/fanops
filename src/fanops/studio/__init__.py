# FanOps Studio — local content-cockpit web UI. Import app.py LAZILY (it pulls Flask); keeping
# this package init Flask-free lets `import fanops.studio` (and the views/actions read models) work
# on a core, no-[studio] install.
