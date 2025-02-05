import scipy.io
import numpy as np
import h5py
import pickle
import torch
import torch.nn as nn
from torch_geometric.data import Dataset, Data
import pdb
from typing import Tuple
import sys, os
sys.path.append(os.path.join(os.path.dirname("__file__"), '..', '..'))
sys.path.append(os.path.join(os.path.dirname("__file__"), '..'))
sys.path.append(os.path.join(os.path.dirname("__file__"), '..', '..', '..'))
from cindm.le_pde.utils import to_tuple_shape, MPPDE1D_PATH, PDE_PATH
from cindm.le_pde.MP_Neural_PDE_Solvers.common.utils import HDF5Dataset
from cindm.le_pde.MP_Neural_PDE_Solvers.equations.PDEs import *



class MPPDE1D(Dataset):
    def __init__(
        self,
        dataset="mppde1d-E1-100",
        input_steps=1,
        output_steps=1,
        time_interval=1,
        is_y_diff=False,
        is_1d_periodic=True,
        is_normalize_pos=False,
        split="train",
        transform=None,
        pre_transform=None,
        verbose=False,
    ):
        assert dataset.startswith("mppde1d")
        self.dataset = dataset
        self.dirname = MPPDE1D_PATH
        self.root = PDE_PATH
        
        if len(dataset.split("-")) == 3:
            _, self.mode, self.nx = dataset.split("-")
            self.nt_total, self.nx_total = 250, 200
        else:
            assert len(dataset.split("-")) == 7
            _, self.mode, self.nx, _, self.nt_total, _, self.nx_total = dataset.split("-")
            self.nt_total, self.nx_total = int(self.nt_total), int(self.nx_total)
        self.nx = int(self.nx)
        self.input_steps = input_steps
        self.output_steps = output_steps
        self.time_interval = time_interval
        self.is_y_diff = is_y_diff
        self.is_1d_periodic = is_1d_periodic
        self.is_normalize_pos = is_normalize_pos
        self.split = split
        assert self.split in ["train", "valid", "test"]
        self.verbose = verbose

        self.t_cushion_input = self.input_steps * self.time_interval if self.input_steps * self.time_interval > 1 else 1
        self.t_cushion_output = self.output_steps * self.time_interval if self.output_steps * self.time_interval > 1 else 1

        self.original_shape = (self.nx,)
        self.dyn_dims = 1  # density
        
        pde=CE(device="cpu")
        if (self.nt_total, self.nx_total) == (250, 200):
            path = os.path.join(PDE_PATH, MPPDE1D_PATH) + f'{pde}_{self.split}_{self.mode}.h5'
        else:
            path = os.path.join(PDE_PATH, MPPDE1D_PATH) + f'{pde}_{self.split}_{self.mode}_nt_{self.nt_total}_nx_{self.nx_total}.h5'
        print(f"Load dataset {path}")
        self.time_stamps = self.nt_total
        base_resolution=[self.nt_total, self.nx]
        super_resolution=[self.nt_total, self.nx_total]

        self.dataset_cache = HDF5Dataset(path, pde=pde, 
                                         mode=self.split, base_resolution=base_resolution, 
                                         super_resolution=super_resolution)
        self.n_simu = len(self.dataset_cache)
        self.time_stamps_effective = (self.time_stamps - self.t_cushion_input - self.t_cushion_output + self.time_interval) // self.time_interval
        super(MPPDE1D, self).__init__(self.root, transform, pre_transform)

    @property
    def raw_file_names(self):
        return []

    @property
    def processed_dir(self):
        return os.path.join(self.root, self.dirname)

    @property
    def processed_file_names(self):
        return []

    def download(self):
        # Download to `self.raw_dir`.
        pass

    def _process(self):
        import warnings
        from typing import Any, List
        from torch_geometric.data.makedirs import makedirs
        def _repr(obj: Any) -> str:
            if obj is None:
                return 'None'
                return re.sub('(<.*?)\\s.*(>)', r'\1\2', obj.__repr__())

        def files_exist(files: List[str]) -> bool:
            # NOTE: We return `False` in case `files` is empty, leading to a
            # re-processing of files on every instantiation.
            return len(files) != 0 and all([os.path.exists(f) for f in files])

        f = os.path.join(self.processed_dir, 'pre_transform.pt')
        if os.path.exists(f) and torch.load(f) != _repr(self.pre_transform):
            warnings.warn(
                f"The `pre_transform` argument differs from the one used in "
                f"the pre-processed version of this dataset. If you want to "
                f"make use of another pre-processing technique, make sure to "
                f"sure to delete '{self.processed_dir}' first")

        f = os.path.join(self.processed_dir, 'pre_filter.pt')
        if os.path.exists(f) and torch.load(f) != _repr(self.pre_filter):
            warnings.warn(
                "The `pre_filter` argument differs from the one used in the "
                "pre-processed version of this dataset. If you want to make "
                "use of another pre-fitering technique, make sure to delete "
                "'{self.processed_dir}' first")

        if files_exist(self.processed_paths):  # pragma: no cover
            return

        makedirs(self.processed_dir)
        self.process()

        path = os.path.join(self.processed_dir, 'pre_transform.pt')
        if not os.path.isfile(path):
            torch.save(_repr(self.pre_transform), path)
        path = os.path.join(self.processed_dir, 'pre_filter.pt')
        if not os.path.isfile(path):
            torch.save(_repr(self.pre_filter), path)

    def get_edge_index(self):
        edge_index_filename = os.path.join(self.processed_dir, f"{self.dataset}_edge_index{'_periodic' if self.is_1d_periodic else ''}.p")
        mask_valid_filename = os.path.join(self.root, self.dirname, f"{self.dataset}_mask_index.p")
        if os.path.isfile(edge_index_filename) and os.path.isfile(mask_valid_filename):
            edge_index = pickle.load(open(edge_index_filename, "rb"))
            mask_valid = pickle.load(open(mask_valid_filename, "rb"))
            return edge_index, mask_valid
        mask_valid = torch.ones(self.original_shape).bool()
        #velo_invalid_ids = np.where(velo_invalid_mask.flatten())[0]
        rows, cols = (*self.original_shape, 1)
        cube = np.arange(rows * cols).reshape(rows, cols)
        edge_list = []
        for i in range(rows):
            for j in range(cols):
                if i + 1 < rows: #and cube[i, j] not in velo_invalid_ids and cube[i+1, j] not in velo_invalid_ids:
                    edge_list.append([cube[i, j], cube[i+1, j]])
                    edge_list.append([cube[i+1, j], cube[i, j]])
                if j + 1 < cols: #and cube[i, j]: #not in velo_invalid_ids and cube[i, j+1] not in velo_invalid_ids:
                    edge_list.append([cube[i, j], cube[i, j+1]])
                    edge_list.append([cube[i, j+1], cube[i, j]])
        if self.is_1d_periodic:
            for j in range(cols):
                edge_list.append([cube[0, j], cube[rows-1, j]])
                edge_list.append([cube[rows-1, j], cube[0, j]])
        edge_index = torch.LongTensor(edge_list).T
        pickle.dump(edge_index, open(edge_index_filename, "wb"))
        pickle.dump(mask_valid, open(mask_valid_filename, "wb"))
        return edge_index, mask_valid

    def process(self):
        pass

    def len(self):
        return self.time_stamps_effective * self.n_simu

    def get(self, idx):
        # assert self.time_interval == 1
        sim_id, time_id = divmod(idx, self.time_stamps_effective)
        _, data_traj, x_pos, param = self.dataset_cache[sim_id]
        if self.verbose:
            print(f"sim_id: {sim_id}   time_id: {time_id}   input: ({time_id * self.time_interval + self.t_cushion_input -self.input_steps * self.time_interval}, {time_id * self.time_interval + self.t_cushion_input})  output: ({time_id * self.time_interval + self.t_cushion_input}, {time_id * self.time_interval + self.t_cushion_input + self.output_steps * self.time_interval})")
        x_dens = torch.FloatTensor(np.stack([data_traj[time_id * self.time_interval + self.t_cushion_input + j] for j in range(-self.input_steps * self.time_interval, 0, self.time_interval)], -1))
        y_dens = torch.FloatTensor(np.stack([data_traj[time_id * self.time_interval + self.t_cushion_input + j] for j in range(0, self.output_steps * self.time_interval, self.time_interval)], -1))  # [1, rows, cols, output_steps, 1]
        edge_index, mask_valid = self.get_edge_index()
        param = torch.cat([torch.FloatTensor([ele]) for key, ele in param.items()])
        x_bdd = torch.ones(x_dens.shape[0])
        x_bdd[0] = 0
        x_bdd[-1] = 0
        x_pos = torch.FloatTensor(x_pos)[...,None]
        if self.is_normalize_pos:
            for dim in range(len(self.original_shape)):
                x_pos[..., dim] /= self.original_shape[dim]

        data = Data(
            x=x_dens.reshape(-1, *x_dens.shape[-1:], 1).clone(),       # [number_nodes: 64 * 64, input_steps, 1]
            x_pos=x_pos,  # [number_nodes: 128 * 128, 2]
            x_bdd=x_bdd[...,None],
            xfaces=torch.tensor([]),
            y=y_dens.reshape(-1, *y_dens.shape[-1:], 1).clone(),       # [number_nodes: 64 * 64, input_steps, 1]
            edge_index=edge_index,
            mask=mask_valid,
            param=param,
            original_shape=self.original_shape,
            dyn_dims=self.dyn_dims,
            compute_func=(0, None),
            dataset=self.dataset,
            is_1d_periodic=self.is_1d_periodic,
            is_normalize_pos=self.is_normalize_pos,
        )
        update_edge_attr_1d(data)
        return data


def update_rel_pos(edge_attr, is_normalize_pos=False):
    assert len(edge_attr.shape) == 2 and edge_attr.shape[1] == 1
    if is_normalize_pos:
        id_he = torch.where((edge_attr.squeeze(-1) + 0.16).abs() < 1e-7)[0]
        id_eh = torch.where((edge_attr.squeeze(-1) - 0.16).abs() < 1e-7)[0]
        edge_attr[id_he, 0] = 0.16/99
        edge_attr[id_eh, 0] = -0.16/99
        id_he16 = torch.where((edge_attr.squeeze(-1) + 16).abs() < 1e-7)[0]
        assert len(id_he16) == 0, "There are {} rel_pos that has value of -16. Check is_normalize_pos".format(len(id_he16))
    else:
        id_he = torch.where((edge_attr.squeeze(-1) + 16).abs() < 1e-7)[0]
        id_eh = torch.where((edge_attr.squeeze(-1) - 16).abs() < 1e-7)[0]
        edge_attr[id_he, 0] = 16/99
        edge_attr[id_eh, 0] = -16/99
        id_he016 = torch.where((edge_attr.squeeze(-1) + 0.16).abs() < 1e-8)[0]
        assert len(id_he016) == 0, "There are {} rel_pos that has value of -0.16. Check args.is_normalize_pos".format(len(id_he016))
    return edge_attr


def update_edge_attr_1d(data):
    dataset_str = to_tuple_shape(data.dataset)
    is_normalize_pos = False if hasattr(data, "is_normalize_pos") and (not to_tuple_shape(data.is_normalize_pos)) else True
    if dataset_str.split("-")[0] != "mppde1d":
        if hasattr(data, "node_feature"):
            edge_attr = data.node_pos["n0"][data.edge_index[("n0","0","n0")][0]] - data.node_pos["n0"][data.edge_index[("n0","0","n0")][1]]
            if hasattr(data, "is_1d_periodic") and to_tuple_shape(data.is_1d_periodic):
                edge_attr = update_rel_pos(edge_attr, is_normalize_pos=is_normalize_pos)
            if dataset_str.split("-")[0] in ["mppde1de"]:
                data.edge_attr = {("n0","0","n0"): edge_attr}
            elif dataset_str.split("-")[0] in ["mppde1df", "mppde1dg", "mppde1dh"]:
                data.edge_attr = {("n0","0","n0"): torch.cat([edge_attr, edge_attr.abs()], -1)}
            else:
                raise
            if dataset_str.split("-")[0] in ["mppde1dg"]:
                data.x_bdd = {"n0": 1 - data.x_bdd["n0"]}
        else:
            edge_attr = data.x_pos[data.edge_index[0]] - data.x_pos[data.edge_index[1]]
            if hasattr(data, "is_1d_periodic") and to_tuple_shape(data.is_1d_periodic):
                edge_attr = update_rel_pos(edge_attr, is_normalize_pos=is_normalize_pos)
            if dataset_str.split("-")[0] in ["mppde1de"]:
                data.edge_attr = edge_attr
            elif dataset_str.split("-")[0] in ["mppde1df", "mppde1dg", "mppde1dh"]:
                data.edge_attr = torch.cat([edge_attr, edge_attr.abs()], -1)
            else:
                raise
            if dataset_str.split("-")[0] in ["mppde1dg"]:
                data.x_bdd = 1 - data.x_bdd
    return data


if __name__ == "__main__":
    dataset = MPPDE1D(
        dataset="mppde1d-E2-100",
        input_steps=1,
        output_steps=1,
        time_interval=1,
        is_y_diff=False,
        is_1d_periodic=True,
        split="valid",
        transform=None,
        pre_transform=None,
        verbose=False,
    )
    import matplotlib.pylab as plt
    plt.plot(dataset[198].x.squeeze())

