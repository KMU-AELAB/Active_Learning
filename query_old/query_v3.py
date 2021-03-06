import os
import random
from tqdm import tqdm

import numpy as np

import torch
from torch import nn
from torch.backends import cudnn
from torch.utils.data import DataLoader
from torchvision import transforms
from torchvision.datasets import CIFAR100, CIFAR10

from .graph.vae_v3 import VAE as vae
from .strategy.strategy_v3 import Strategy

from task.graph.resnet import ResNet18 as resnet
from task.graph.lossnet import LossNet as lossnet

from data.sampler import Sampler

cudnn.benchmark = True


class Query(object):
    def __init__(self, config):
        self.config = config

        self.initial_size = self.config.initial_size
        self.budget = self.config.budge_size
        self.labeled = []
        self.unlabeled = [i for i in range(self.config.data_size)]

        self.batch_size = self.config.vae_batch_size

        # define dataloader
        if 'cifar' in self.config.data_name:
            self.train_transform = transforms.Compose([
                transforms.ToTensor(),
                transforms.Normalize([0.4914, 0.4822, 0.4465], [0.2023, 0.1994, 0.2010]),
            ])

            if self.config.data_name == 'cifar10':
                self.dataset = CIFAR10(os.path.join(self.config.root_path, self.config.data_directory),
                                       train=True, download=True, transform=self.train_transform)
            elif self.config.data_name == 'cifar100':
                self.dataset = CIFAR100(os.path.join(self.config.root_path, self.config.data_directory),
                                        train=True, download=True, transform=self.train_transform)

        # define models
        self.vae = vae(self.config.vae_num_hiddens, self.config.vae_num_residual_layers,
                       self.config.vae_num_residual_hiddens, self.config.vae_num_embeddings,
                       self.config.vae_embedding_dim, self.config.vae_commitment_cost, self.config.vae_distance,
                       self.config.vae_decay).cuda()
        self.task = resnet(self.config.num_classes).cuda()
        self.loss_module = lossnet().cuda()

        # parallel setting
        gpu_list = list(range(self.config.gpu_cnt))
        self.vae = nn.DataParallel(self.vae, device_ids=gpu_list)
        self.task = nn.DataParallel(self.task, device_ids=gpu_list)
        self.loss_module = nn.DataParallel(self.loss_module, device_ids=gpu_list)

    def load_checkpoint(self, step_cnt):
        try:
            filename = os.path.join(self.config.root_path, self.config.checkpoint_directory, 'vae.pth.tar')
            print("Loading checkpoint '{}'".format(filename))
            checkpoint = torch.load(filename)

            self.vae.load_state_dict(checkpoint['vae_state_dict'])

            if step_cnt > 0:
                filename = os.path.join(self.config.root_path, self.config.checkpoint_directory, 'task.pth.tar')
                print("Loading checkpoint '{}'".format(filename))
                checkpoint = torch.load(filename)

                self.task.load_state_dict(checkpoint['task_state_dict'])
                self.loss_module.load_state_dict(checkpoint['loss_state_dict'])

        except OSError as e:
            print("No checkpoint exists from '{}'. Skipping...".format(self.config.checkpoint_directory))
            print("**First time to train**")

    def training(self):
        strategy = Strategy(self.config)
        strategy.run()

    def sampling(self, step_cnt):
        sample_size = self.budget if step_cnt else self.initial_size

        if not step_cnt:
            self.training()

        self.load_checkpoint(step_cnt)

        dataloader = DataLoader(self.dataset, batch_size=self.batch_size,
                                pin_memory=self.config.pin_memory, sampler=Sampler(self.unlabeled))
        tqdm_batch = tqdm(dataloader, total=len(dataloader))

        index = 0
        data_dict = {}
        with torch.no_grad():
            for curr_it, data in enumerate(tqdm_batch):
                self.vae.eval()
                self.task.eval()
                self.loss_module.eval()

                data = data[0].cuda(async=self.config.async_loading)

                _, _, encoding_indices = self.vae(data)
                _, features = self.task(data)
                pred_loss = self.loss_module(features)
                
                encoding_indices = encoding_indices.cpu().numpy()
                pred_loss = pred_loss.view([-1, ]).cpu().numpy()
                
                for idx in range(len(encoding_indices)):
                    if encoding_indices[idx] in data_dict:
                        data_dict[encoding_indices[idx]].append([pred_loss[idx], self.unlabeled[index]])
                    else:
                        data_dict[encoding_indices[idx]] = [[pred_loss[idx], self.unlabeled[index]]]
                    index += 1

            tqdm_batch.close()
            
        for i in data_dict:
            print(i, len(data_dict[i]), end=' / ')
        print()

        sample_set = []
        total_remain = []
        quota = int(sample_size / len(data_dict))
        for i in data_dict:
            if step_cnt:
                tmp_list = sorted(data_dict[i], key=lambda x: x[0], reverse=True)

                if len(tmp_list) > quota:
                    sample_set += list(np.array(tmp_list)[:quota, 1])
                    total_remain += tmp_list[quota:]
                else:
                    sample_set += list(np.array(tmp_list)[:, 1])

            else:
                tmp_list = set(np.array(data_dict[i])[:, 1])

                if len(tmp_list) > quota:
                    sampled = random.sample(tmp_list, quota)
                    sample_set += sampled
                    total_remain += list(tmp_list - set(sampled))
                else:
                    sample_set += list(tmp_list)

        if step_cnt:
            tmp_list = sorted(total_remain, key=lambda x: x[0], reverse=True)
            sample_set += list(np.array(tmp_list)[:(sample_size - len(sample_set)), 1])

        else:
            sample_set += random.sample(total_remain, sample_size - len(sample_set))

        print('cent cnt:', len(set(data_dict.keys())))

        if len(set(sample_set)) < sample_size:
            print('!!!!!!!!!!!!!!!! error !!!!!!!!!!!!!!!!', len(set(sample_set)))

        self.labeled += sample_set
        self.unlabeled = list(set(self.unlabeled) - set(sample_set))
