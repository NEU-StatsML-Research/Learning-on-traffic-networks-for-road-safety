import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader

import numpy as np
from evaluators import eval_mae
from data_loaders import load_yearly_data
from logger import Logger

class VolumeRegressionTrainer:

    def __init__(self, model, predictor, data, optimizer, 
                 data_dir, state_name,
                 train_years, valid_years, test_years,
                 epochs, batch_size, eval_steps, device,
                 log_metrics = ['MAE', 'MSE'],
                 use_dynamic_node_features = False,
                 use_dynamic_edge_features = False, # deprecated
                 num_negative_edges = 10000, # deprecated
                 node_feature_mean = None,
                 node_feature_std = None,
                 edge_feature_mean = None,  # deprecated
                 edge_feature_std = None # deprecated
                 ): 
        self.model = model
        self.predictor = predictor
        self.data = data
        self.optimizer = optimizer

        self.data_dir = data_dir
        self.state_name = state_name

        self.train_years = train_years
        self.valid_years = valid_years
        self.test_years = test_years

        self.epochs = epochs
        self.batch_size = batch_size
        self.eval_steps = eval_steps
        self.device = device

        self.use_dynamic_node_features = use_dynamic_node_features
        self.use_dynamic_edge_features = use_dynamic_edge_features
        self.num_negative_edges = num_negative_edges

        # collecting dynamic features normlization statistics
        if node_feature_mean is None and (self.use_dynamic_node_features or self.use_dynamic_edge_features):
            self.node_feature_mean, self.node_feature_std, self.edge_feature_mean, self.edge_feature_std = self.compute_feature_mean_std()
        else:
            self.node_feature_mean = node_feature_mean
            self.node_feature_std = node_feature_std
            self.edge_feature_mean = edge_feature_mean
            self.edge_feature_std = edge_feature_std

        self.loggers = {
            key: Logger(runs=1) for key in log_metrics
        }

    def train_on_year_data(self, year): 
        pos_edges, pos_edge_weights, node_features = load_yearly_data(data_dir=self.data_dir, state_name=self.state_name, year=year)
        
        if pos_edges is None or pos_edges.size(0) < 10:
            return 0, 0
        
        # normalize node and edge features
        if self.node_feature_mean is not None:
            node_features = (node_features - self.node_feature_mean) / self.node_feature_std
        

        new_data = self.data.clone()
        if self.use_dynamic_node_features:
            if new_data.x is None:
                new_data.x = node_features
            else:
                new_data.x = torch.cat([new_data.x, node_features], dim=1)
        
        self.model.train()
        self.predictor.train()

        # encoding
        new_data = new_data.to(self.device)
        h = self.model(new_data.x, new_data.edge_index, new_data.edge_attr)
        edge_attr = new_data.edge_attr

        # predicting
        pos_edge_weights = pos_edge_weights.to(self.device)
        pos_train_edge = pos_edges.to(self.device)
        total_loss = total_examples = 0
        for perm in DataLoader(range(pos_train_edge.size(0)), self.batch_size, shuffle=True):
            self.optimizer.zero_grad()
            # positive edges
            edge = pos_train_edge[perm].t()
            pos_out = self.predictor(h[edge[0]], h[edge[1]]) \
                if edge_attr is None else \
                self.predictor(h[edge[0]], h[edge[1]], edge_attr[perm])
            
            labels = pos_edge_weights.view(-1, 1).to(self.device)
            # print(pos_out)
            # print(labels)
            loss = F.l1_loss(pos_out, labels)
            loss.backward(retain_graph=True)
            self.optimizer.step()
            
            num_examples = pos_out.size(0)
            total_loss += loss.item() * num_examples
            total_examples += num_examples
        
        return total_loss, total_examples

    @torch.no_grad()
    def test_on_year_data(self, year):
        pos_edges, pos_edge_weights, node_features = load_yearly_data(data_dir=self.data_dir, state_name=self.state_name, year=year)

        if pos_edges is None or pos_edges.size(0) < 10:
            return {}, 0

        print(f"Eval on {year} data")
        print(f"Number of edges with valid traffic volume: {pos_edges.size(0)}")

        # normalize node and edge features
        if self.node_feature_mean is not None:
            node_features = (node_features - self.node_feature_mean) / self.node_feature_std

        new_data = self.data.clone()
        if self.use_dynamic_node_features:
            if new_data.x is None:
                new_data.x = node_features
            else:
                new_data.x = torch.cat([new_data.x, node_features], dim=1)

        self.model.eval()
        self.predictor.eval()

        # encoding
        new_data = new_data.to(self.device)
        h = self.model(new_data.x, new_data.edge_index, new_data.edge_attr)
        edge_attr = new_data.edge_attr

        # predicting
        pos_edge = pos_edges.to(self.device)
        pos_preds = []
        for perm in DataLoader(range(pos_edge.size(0)), self.batch_size):
            edge = pos_edge[perm].t()
            preds = self.predictor(h[edge[0]], h[edge[1]]) \
                if edge_attr is None else \
                self.predictor(h[edge[0]], h[edge[1]], edge_attr[perm])
            pos_preds += [preds.squeeze().cpu()] 
        pos_preds = torch.cat(pos_preds, dim=0)

        # Eval ROC-AUC
        results = eval_mae(pos_preds, pos_edge_weights)

        return results, pos_edges.size(0)


    def train_epoch(self):
        total_loss = total_examples = 0
        for year in self.train_years:
            loss, samples = self.train_on_year_data(year)
            total_loss += loss
            total_examples += samples
        return total_loss/total_examples
    

    def train(self):
        train_log = {}
        for epoch in range(1, 1 + self.epochs):
            loss = self.train_epoch()

            if epoch % self.eval_steps == 0:
                results = self.test()
                for key, result in results.items():
                    self.loggers[key].add_result(run=0, result=result)
            
                for key, result in results.items():
                    train_hits, valid_hits, test_hits = result
                    print(key)
                    print(f'Epoch: {epoch:02d}, '
                          f'Loss: {loss:.4f}, '
                          f'Train: {train_hits:.4f}, '
                          f'Valid: {valid_hits:.4f}, '
                          f'Test: {test_hits:.4f}')
                print('---')

        for key in self.loggers.keys():
            print(key)
            mode = 'min' if (key == 'Loss' or key == "MAE" or key == "MSE") else 'max'
            train, valid, test = self.loggers[key].print_statistics(run=0, mode=mode)
            train_log[f"Train_{key}"] = train
            train_log[f"Valid_{key}"] = valid
            train_log[f"Test_{key}"] = test
        return train_log

    def test(self):
        train_results = {}; train_size = 0
        for year in self.train_years:
            month_results, month_sample_size = self.test_on_year_data(year)
            for key, value in month_results.items():
                if key not in train_results:
                    train_results[key] = 0
                train_results[key] += value * month_sample_size
            train_size += month_sample_size

        for key, value in train_results.items():
            train_results[key] = value / train_size

        val_results = {}; val_size = 0
        for year in self.valid_years:
            month_results, month_sample_size = self.test_on_year_data(year)
            for key, value in month_results.items():
                if key not in val_results:
                    val_results[key] = 0
                val_results[key] += value * month_sample_size
            val_size += month_sample_size
        
        for key, value in val_results.items():
            val_results[key] = value / val_size

        test_results = {}; test_size = 0
        for year in self.test_years:
            month_results, month_sample_size = self.test_on_year_data(year)
            for key, value in month_results.items():
                if key not in test_results:
                    test_results[key] = 0
                test_results[key] += value * month_sample_size
            test_size += month_sample_size
            
        for key, value in test_results.items():
            test_results[key] = value / test_size

        results = {}
        for key in train_results.keys():
            results[key] = (train_results[key], val_results[key], test_results[key])
        return results
    
    def compute_feature_mean_std(self):
        all_node_features = []
        # all_edge_features = []
        for year in self.train_years:
            _, _, node_features = \
                load_yearly_data(data_dir=self.data_dir, state_name=self.state_name, year=year)
            all_node_features.append(node_features)
        
        all_node_features = torch.cat(all_node_features, dim=0)
        # all_edge_features = torch.cat(all_edge_features, dim=0)

        node_feature_mean, node_feature_std = all_node_features.mean(dim=0), all_node_features.std(dim=0)
        # edge_feature_mean, edge_feature_std = all_edge_features.mean(dim=0), all_edge_features.std(dim=0)
        return node_feature_mean, node_feature_std, None, None
    
    def get_feature_stats(self):
        return self.node_feature_mean, self.node_feature_std, self.edge_feature_mean, self.edge_feature_std