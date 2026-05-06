import copy
import json
from pathlib import Path


class SubscriptionStore:
    def __init__(self, data_dir: str):
        self.path = Path(data_dir) / "subscriptions.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"subscriptions": {}}

    def _save(self) -> None:
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")

    def add(self, origin: str, mid: str, name: str, last_bvid: str = "") -> bool:
        subs = self.data.setdefault("subscriptions", {})
        bucket = subs.setdefault(origin, {"up_list": []})
        if any(item["mid"] == mid for item in bucket["up_list"]):
            return False
        bucket["up_list"].append({"mid": mid, "name": name, "last_bvid": last_bvid})
        self._save()
        return True

    def remove(self, origin: str, mid: str) -> bool:
        subs = self.data.setdefault("subscriptions", {})
        if origin not in subs:
            return False
        old_len = len(subs[origin]["up_list"])
        subs[origin]["up_list"] = [item for item in subs[origin]["up_list"] if item["mid"] != mid]
        if len(subs[origin]["up_list"]) == old_len:
            return False
        if not subs[origin]["up_list"]:
            del subs[origin]
        self._save()
        return True

    def list_for(self, origin: str) -> list[dict]:
        return copy.deepcopy(self.data.get("subscriptions", {}).get(origin, {}).get("up_list", []))

    def all(self) -> dict[str, list[dict]]:
        return {
            origin: copy.deepcopy(payload.get("up_list", []))
            for origin, payload in self.data.get("subscriptions", {}).items()
        }

    def update_last(self, origin: str, mid: str, bvid: str) -> None:
        for item in self.data.get("subscriptions", {}).get(origin, {}).get("up_list", []):
            if item["mid"] == mid:
                item["last_bvid"] = bvid
                self._save()
                return

