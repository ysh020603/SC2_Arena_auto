class IterativeMean:
    def __init__(self):
        self.mean = 0
        self.count = 0

    def update(self, new_value):
        self.count += 1
        self.mean = self.mean + (new_value - self.mean) / self.count
        return self.mean
