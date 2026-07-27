"""smallFish FastAPI backend.

Serves stock, ETF, portfolio, sector, options, retirement, and Research Studies
data to the Angular dashboard, and hosts the built UI from ``stock-app/static``.

This package is intentionally self-contained: it consumes stable generated data
artifacts under ``SFP_DATA_DIR`` rather than importing the utility runtime.
``models/`` is the only shared in-repo dependency. In particular, ``/studies``
reads materialized ``data/studies`` JSON through ``models.study``; it never
imports the study or utility runtime.

See ``stock-app/README.md`` for the API layout and ``docs/ARCHITECTURE.md`` for
the allowed dependency direction.
"""
