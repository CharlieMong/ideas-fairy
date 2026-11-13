"""Recently-published CVEs via the NVD 2.0 API, to surface emerging technique classes."""
from __future__ import annotations

import datetime as _dt
from typing import Any

import httpx

from ..models import Item
from .base import Source

NVD_API = "https://services.nvd.nist.gov/rest/json/cves/2.0"


class NvdSource(Source):
    source_kind = "advisory"

    def collect(self, client: httpx.Client) -> tuple[list[Item], dict[str, Any]]:
        lookback = int(self.scfg.get("lookback_days", 7))
        min_cvss = float(self.scfg.get("min_cvss", 7.0))
        now = _dt.datetime.now(_dt.timezone.utc)
        start = now - _dt.timedelta(days=lookback)
        params = {
            "pubStartDate": start.strftime("%Y-%m-%dT%H:%M:%S.000"),
            "pubEndDate": now.strftime("%Y-%m-%dT%H:%M:%S.000"),
            "resultsPerPage": str(min(self.limit, 100)),
        }
        items: list[Item] = []
        raw: dict[str, Any] = {}
        try:
            resp = client.get(NVD_API, params=params)
            resp.raise_for_status()
        except httpx.HTTPError:
            return items, raw
        for entry in resp.json().get("vulnerabilities", []):
            cve = entry.get("cve", {})
            cvss = self._best_cvss(cve)
            if cvss is not None and cvss < min_cvss:
                continue
            cve_id = cve.get("id", "")
            descr = ""
            for d in cve.get("descriptions", []):
                if d.get("lang") == "en":
                    descr = d.get("value", "")
                    break
            it = Item(
                source=self.name,
                source_kind=self.source_kind,
                title=f"{cve_id} (CVSS {cvss})" if cvss else cve_id,
                url=f"https://nvd.nist.gov/vuln/detail/{cve_id}",
                summary=descr,
                published=self._iso(cve.get("published")),
                method="nvd_api",
                tags=[cve_id] + ([f"cvss:{cvss}"] if cvss else []),
                extra={"cvss": cvss},
            )
            raw[it.evidence_id] = cve
            items.append(it)
        return items, raw

    @staticmethod
    def _best_cvss(cve: dict) -> float | None:
        metrics = cve.get("metrics", {})
        for key in ("cvssMetricV31", "cvssMetricV30", "cvssMetricV2"):
            arr = metrics.get(key)
            if arr:
                try:
                    return float(arr[0]["cvssData"]["baseScore"])
                except (KeyError, IndexError, ValueError, TypeError):
                    continue
        return None
