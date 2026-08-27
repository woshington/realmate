from pathlib import Path

from properties.importers.base import BaseImporter


class JSONImporter(BaseImporter):
    def __init__(self, path: Path):
        self.path = path

    def load(self):
        raise NotImplementedError