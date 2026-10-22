# Vendored browser libraries

These files are served by the dashboard itself so it works without internet access.
Both are MIT licensed; the license banners are kept in the files.

| File | Project | Version | Source |
|------|---------|---------|--------|
| `chart-4.4.2.umd.min.js` | [Chart.js](https://www.chartjs.org) | 4.4.2 | `https://cdn.jsdelivr.net/npm/chart.js@4.4.2/dist/chart.umd.min.js` |
| `chartjs-adapter-date-fns-3.0.0.bundle.min.js` | [chartjs-adapter-date-fns](https://github.com/chartjs/chartjs-adapter-date-fns) (bundles date-fns) | 3.0.0 | `https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns@3.0.0/dist/chartjs-adapter-date-fns.bundle.min.js` |

The only local change is that the trailing `//# sourceMappingURL=...` comment was removed from
the Chart.js file (the map is not vendored, so browsers would log a 404 in DevTools).

To upgrade, download the new version, update the filename and the `<script>` tags in
`templates/index.html`, and update this table.
