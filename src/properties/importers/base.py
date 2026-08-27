from abc import abstractmethod, ABC


class BaseImporter(ABC):
    @abstractmethod
    def load(self):
        raise NotImplementedError