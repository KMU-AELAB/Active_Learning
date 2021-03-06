import random
import torch
from torch.backends import cudnn

from config import Config
from query.query_v2 import Query
from query.strategy.strategy_v2 import Strategy
from task.classification_loss import ClassificationWithLoss as Task

cudnn.deterministic = True

random.seed(9410)
torch.manual_seed(9410)
torch.cuda.manual_seed_all(9410)


def main(cycle_cnt):
    config = Config()
    query = Query(config)
    strategy = Strategy(config)
    task = Task(config)

    fp = open(f'record_{cycle_cnt}.txt', 'w')
    for step_cnt in range(config.max_cycle):
        # take a new sample
        query.sampling(step_cnt, strategy, task)

        # train a task model
        task.run(query.labeled)

        print(f'trial-{cycle_cnt} / step {step_cnt + 1}: train data count - {len(set(query.labeled))}')
        print(f'test accuracy - {task.best_acc}')

        fp.write(f'{task.best_acc}\n')

        if step_cnt < config.max_cycle - 1:
            strategy.run(task, query.labeled)

    fp.close()


if __name__ == '__main__':
    for i in range(10):
        main(i + 1)
