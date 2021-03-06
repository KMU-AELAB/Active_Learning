from torch.utils import data


class Sampler(data.Sampler):
    def __init__(self, indices):
        self.indices = indices

    def __iter__(self):
        return (int(self.indices[i]) for i in range(len(self.indices)))

    def __len__(self):
        return len(self.indices)
