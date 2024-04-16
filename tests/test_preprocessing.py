#!/usr/bin/env python

from pathlib import Path
import numpy as np
import pandas as pd
from scipy.sparse import coo_matrix

import pytest
from numpy.testing import assert_equal, assert_array_equal

from schpf import preprocessing as prep


TXT = str(Path(__file__).parent / \
        Path('_data/PJ030merge.c300t400_g0t500.matrix.txt'))
NCELLS = 100
NGENES = 500

# TODO figure out how to get this without going this far up tree or doubling
# perhaps make a small copy?
PROTEIN_CODING = str(
    Path(*Path(__file__).parts[:-2]) / Path(
        'resources/gencode.v29.annotation.gene_l1l2.pc_TRC_IGC.stripped.txt'))
BLIST = str( Path(__file__).parent /  Path('_data/sample_blacklist.txt') )


@pytest.fixture()
def protein_coding():
    return pd.read_csv(PROTEIN_CODING, sep=r'\s+', header=None)


@pytest.fixture()
def blacklist():
    return pd.read_csv(BLIST, sep=r'\s+', header=None)


@pytest.fixture()
def exp_genes():
    return pd.read_csv(TXT, sep=r'\s+', header=None)[[0,1]]


@pytest.mark.parametrize('ngene_cols', [2,3])
def test_load_txt(ngene_cols):
    coo, genes = prep.load_txt(TXT, ngene_cols)
    assert genes.shape[1] == ngene_cols
    assert coo.shape[1] == NGENES
    assert genes.shape[0] == NGENES
    assert coo.shape[0]  == NCELLS + 2 - ngene_cols


# TODO add loom and test, also check that this works since passed shouldn't?
def test_load_like(tmp_path):
    gene_file = str(tmp_path / 'genes.txt')

    # make a permutation
    perm = np.random.choice(NGENES, NGENES-10, replace=False)

    # load data to make reference and permute
    umis, genes = prep.load_txt(TXT)
    umis = umis.A[:, perm]
    genes = genes.loc[perm]

    # write permuted/subsampled reference file
    genes.to_csv(gene_file, header=None, sep='\t', index=None)

    # load like permuted reference
    ll_umi, ll_genes = prep.load_like(TXT, reference=gene_file)
    assert_equal(len(ll_genes), len(perm))
    assert_array_equal(umis, ll_umi.A)

    # repeat with no_split_on_dot
    ll_umi, ll_genes = prep.load_like(TXT, reference=gene_file,
            no_split_on_dot=True)
    assert_equal(len(ll_genes), len(perm))
    assert_array_equal(umis, ll_umi.A)

    # by gene name
    ll_umi, ll_genes = prep.load_like(TXT, reference=gene_file,
            by_gene_name=True)
    assert_equal(len(ll_genes), len(perm))
    assert_array_equal(umis, ll_umi.A)

    # corrupt the permuted reference
    bad_genes = genes.copy()
    bad_genes.loc[5, 0] = 'random'
    bad_genes.to_csv(gene_file, header=None, sep='\t', index=None)
    with pytest.raises(ValueError):
        ll_umi, ll_genes = prep.load_like(TXT, reference=gene_file)


def test_min_cells_expressing(data):
    ncells, ngenes = data.shape
    # test all true when 0
    min_cells = 0
    assert_equal(prep.min_cells_expressing_mask(data, min_cells).sum(),
                 ngenes)

    # test all false when > ncell
    min_cells = ngenes + 1
    assert_equal(prep.min_cells_expressing_mask(data, min_cells).sum(),
                 0)
    min_cells = 0.9999999
    assert min_cells < 1
    assert_equal(prep.min_cells_expressing_mask(data, min_cells).sum(),
                 0)

    # test for reasonable value
    min_cells = 5
    n_expressing = data.astype(bool).sum(axis=0).A[0, :]
    mask = n_expressing >= min_cells
    assert_array_equal(prep.min_cells_expressing_mask(data, min_cells),
                       mask)
    # test same for proportion
    min_cells_prop = min_cells / ncells
    assert_array_equal(prep.min_cells_expressing_mask(data, min_cells_prop),
                       mask)


def test_genelist_mask(protein_coding, exp_genes):
    shared_ens = exp_genes[0].str.split('.').str[0].isin(
            protein_coding[0].str.split('.').str[0])
    shared_gene = exp_genes[1].isin(protein_coding[1])

    # whitelist
    assert_array_equal(prep.genelist_mask(exp_genes[0], protein_coding[0]),
                       shared_ens)
    assert_array_equal(prep.genelist_mask(exp_genes[1], protein_coding[1]),
                       shared_gene)

    # blacklist
    assert_array_equal(prep.genelist_mask(exp_genes[0], protein_coding[0],
                                          whitelist=False),
                       ~shared_ens)
    assert_array_equal(prep.genelist_mask(exp_genes[1], protein_coding[1],
                                          whitelist=False),
                       ~shared_gene)


def test_subsample_cell_ixs():
    # int for choices
    assert_equal(len(prep.subsample_cell_ixs(20, 10)),  10)
    # array of choices
    assert_equal(len(prep.subsample_cell_ixs(np.arange(20), 10)),  10)

    # test picks one from a group
    group_ids = np.array([0] * 100 + [1,1])
    idx = prep.subsample_cell_ixs(102, 10, group_ids=group_ids,
            max_group_frac=0.5)
    assert (100 in idx) ^ (101 in idx) #xor
    assert_equal(len(idx), 10)

    # test doesn't pick when can't under constraint
    group_ids = np.array([0] * 18 + [1,1])
    idx = prep.subsample_cell_ixs(20, 5, group_ids=group_ids,
            max_group_frac=0.4)
    assert (not 18 in idx) and (not 19 in idx) #neither of the group 1 indexes
    assert_equal(len(idx), 5) # but still have 5 items


    # test doesn't pick more than it can under constraint
    group_ids = np.array([0] * 18 + [1,1])
    idx = prep.subsample_cell_ixs(20, 5, group_ids=group_ids,
            max_group_frac=0.25)
    assert (not 18 in idx) and (not 19 in idx) #neither of the group 1 indexes
    assert_equal(len(idx), 4) # should have floor(0.25*18) items
    with pytest.warns(UserWarning) as record:
        idx = prep.subsample_cell_ixs(20, 5, group_ids=group_ids,
                max_group_frac=0.25)
    assert len(record) == 1


def test_load_and_filter(protein_coding, blacklist):
    filtered_m2, genes_m2 = prep.load_and_filter(TXT, min_cells=2,
            whitelist=PROTEIN_CODING, blacklist=BLIST)
    assert_equal(filtered_m2.shape[0], NCELLS)
    assert filtered_m2.shape[1] <= NGENES
    assert_equal(filtered_m2.shape[1], len(genes_m2))
    assert_equal(genes_m2[0].str.split('.').str[0].isin(
                    blacklist[0].str.split('.').str[0]).sum(),
                 0)
    assert_equal(genes_m2[0].str.split('.').str[0].isin(
                    protein_coding[0].str.split('.').str[0]).sum(),
                 len(genes_m2))

    filtered_m5, genes_m5 = prep.load_and_filter(TXT, min_cells=5,
            whitelist=PROTEIN_CODING, blacklist=BLIST)
    assert filtered_m5.shape[1] <= filtered_m2.shape[1]
    assert np.all(filtered_m5.astype(bool).sum(axis=0).A >= 5)
