from properties.importers.base import BaseImporter

class APIImporter(BaseImporter):
    def __init__(self):
        pass

    def load(self):
        raise NotImplementedError