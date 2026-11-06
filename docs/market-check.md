# CV vs market

[← README](../README.md)

The "Резюме vs рынок" page of the web UI (`/market`, API `GET /api/market`), code — `jobhunter/market_check.py`.
A local ATS checker: the CV is compared not with an abstract list of skills but with the matching vacancies of a
specific day (`state/jobs/<date>.json`, the archive of every report with texts).

- which technologies the market asks for and whether they are in your PDF CV (`[cv].files` in `settings.toml`)
  and in `cv/base.toml`; what is in the base but did not get into the PDF;
- what the matching vacancies are called and whether your CV headline matches;
- bullet hygiene: numbers, a verb at the start, length; contacts and time zone / location;
- conclusions.

Nothing is sent anywhere: everything is computed from local files.
