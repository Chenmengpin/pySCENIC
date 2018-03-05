# -*- coding: utf-8 -*-

import argparse
import os
from dask.diagnostics import ProgressBar
from multiprocessing import cpu_count
from arboretum.algo import grnboost2
from arboretum.utils import load_tf_names
from .utils import load_from_yaml, save_to_yaml
from .rnkdb import open
from .prune import prune_targets as prune, find_motifs as motifs
from .aucell import create_rankings, enrichment
from .genesig import GeneSignature
import pandas as pd
import datetime
import sys


class NoProgressBar:
    def __enter__(self):
        return self

    def __exit__(*x):
        pass


def add_recovery_parameters(parser):
    group = parser.add_argument_group('motif enrichment')
    group.add_argument('--rank_threshold',
                       type=int, default=5000,
                       help='The rank threshold used for deriving the target genes of an enriched motif.')
    group.add_argument('--auc_threshold',
                       type=float, default=0.05,
                       help='The threshold used for calculating the AUC of a feature as fraction of ranked genes.')
    group.add_argument('--nes_threshold',
                       type=float, default=3.0,
                       help='The Normalized Enrichment Score (NES) threshold for finding enriched features.')
    return parser


def add_annotation_parameters(parser):
    group = parser.add_argument_group('motif annotation')
    group.add_argument('--min_orthologous_identity',
                       type=float, default=0.0,
                       help='Minimum orthologous identity to use when annotating enriched motifs.')
    group.add_argument('--max_similarity_fdr',
                       type=float, default=0.001,
                       help='Maximum FDR in motif similarity to use when annotating enriched motifs.')
    group.add_argument('--annotations_fname',
                       type=argparse.FileType('r'),
                       help='The name of the file that contains the motif annotations to use.')
    return parser


def add_computation_parameters(parser):
    group = parser.add_argument_group('computation')
    group.add_argument('--num_workers',
                       type=int, default=cpu_count(),
                       help='The number of workers to use.')
    group.add_argument('--chunk_size',
                       type=int, default=100,
                       help='The size of the module chunks assigned to a node in the dask graph.')
    group.add_argument('--mode',
                       choices=['custom_multiprocessing', 'dask_multiprocessing', 'dask_cluster'],
                       default='custom_multiprocessing',
                       help='The mode to be used for computing.')
    group.add_argument('--client_or_address',
                       type=float, default='local',
                       help='The client or the IP address of the dask scheduler to use.')
    return parser


def find_modules(args):
    print("{} - Loading datasets.".format(datetime.datetime.now()))
    ex_matrix = pd.read_csv(args.expression_mtx_fname, sep='\t', header=0, index_col=0).T
    tf_names = load_tf_names(args.tfs_fname.name)

    print("{} - Calculating co-expression modules.".format(datetime.datetime.now()))
    network = grnboost2(expression_data=ex_matrix, tf_names=tf_names, verbose=True)

    print("{} - Writing results to file.".format(datetime.datetime.now()))
    network.to_csv(args.output, index=False, sep='\t')


def find_motifs(args):
    # Loading from YAML is extremely slow. Therefore this is a potential performance improvement.
    # Potential improvements are switching to JSON or to use a CLoader:
    # https://stackoverflow.com/questions/27743711/can-i-speedup-yaml
    modules = load_from_yaml(args.module_fname.name) if args.module_fname.name.lower().endswith('.gmt') \
        else GeneSignature.from_gmt(args.module_fname.name, args.nomenclature)
    nomenclature = modules[0].nomenclature

    print("{} - Loading databases.".format(datetime.datetime.now()))
    def name(fname):
        return os.path.basename(fname).split(".")[0]
    dbs = [open(fname=fname, name=name(fname), nomenclature=nomenclature) for fname in args.database_fname]

    print("{} - Calculating regulomes.".format(datetime.datetime.now()))
    motif_annotations_fname = args.annotations_fname.name
    with ProgressBar() if args.mode == "dask_multiprocessing" else NoProgressBar():
        df = motifs(dbs, modules, motif_annotations_fname,
                   rank_threshold=args.rank_threshold,
                   auc_threshold=args.auc_threshold,
                   nes_threshold=args.nes_threshold,
                   client_or_address=args.mode,
                   module_chunksize=args.chunk_size,
                   num_workers=args.num_workers)

    print("{} - Writing results to file.".format(datetime.datetime.now()))
    df.to_csv(args.output)


def prune_targets(args):
    # Loading from YAML is extremely slow. Therefore this is a potential performance improvement.
    # Potential improvements are switching to JSON or to use a CLoader:
    # https://stackoverflow.com/questions/27743711/can-i-speedup-yaml
    modules = load_from_yaml(args.module_fname.name)
    nomenclature = modules[0].nomenclature

    print("{} - Loading databases.".format(datetime.datetime.now()))
    def name(fname):
        return os.path.basename(fname).split(".")[0]
    dbs = [open(fname=fname, name=name(fname), nomenclature=nomenclature) for fname in args.database_fname]

    print("{} - Calculating regulomes.".format(datetime.datetime.now()))
    motif_annotations_fname = args.annotations_fname.name
    with ProgressBar() if args.mode == "dask_multiprocessing" else NoProgressBar():
        out = prune(dbs, modules, motif_annotations_fname,
                           rank_threshold=args.rank_threshold,
                           auc_threshold=args.auc_threshold,
                           nes_threshold=args.nes_threshold,
                           output=args.output_type,
                           client_or_address=args.mode,
                           module_chunksize=args.chunk_size,
                           num_workers=args.num_workers)

    print("{} - Writing results to file.".format(datetime.datetime.now()))
    if args.output_type == 'df':
        out.to_csv(args.output)
    else:
        save_to_yaml(out, args.output.name)


def aucell(args):
    print("{} - Loading datasets.".format(datetime.datetime.now()))
    ex_mtx = pd.read_csv(args.expression_mtx_fname, sep='\t', header=0, index_col=0)
    regulomes = load_from_yaml(args.regulomes_fname.name)

    print("{} - Create rankings.".format(datetime.datetime.now()))
    rnk_mtx = create_rankings(ex_mtx)

    print("{} - Calculating enrichment.".format(datetime.datetime.now()))
    auc_heatmap = pd.concat([enrichment(rnk_mtx.T, regulome) for regulome in regulomes]).unstack('Regulome')

    print("{} - Writing results to file.".format(datetime.datetime.now()))
    auc_heatmap.to_csv(args.output)


def create_argument_parser():
    parser = argparse.ArgumentParser(prog='SCENIC - Single-CEll regulatory Network Inference and Clustering',
                                     fromfile_prefix_chars='@')

    # General options ...
    parser.add_argument('-o', '--output',
                        type=argparse.FileType('w'), default=sys.stdout,
                        help='Output file/stream.')

    subparsers = parser.add_subparsers(help='sub-command help')

    # create the parser for the "grn" command
    parser_grn = subparsers.add_parser('grn',
                                         help='Derive co-expression modules from expression matrix.')
    parser_grn.add_argument('expression_mtx_fname',
                               type=argparse.FileType('r'),
                               help='The name of the file that contains the expression matrix (CSV).')
    parser_grn.add_argument('tfs_fname',
                               type=argparse.FileType('r'),
                               help='The name of the file that contains the list of transcription factors (TXT).')
    add_computation_parameters(parser_grn)
    parser_grn.set_defaults(func=find_modules)

    # create the parser for the "motifs" command
    parser_motifs = subparsers.add_parser('motifs',
                                         help='Find enriched motifs for gene signatures.')
    parser_motifs.add_argument('signatures_fname',
                              type=argparse.FileType('r'),
                              help='The name of the file that contains the gene signatures (GMT or YAML).')
    parser_motifs.add_argument('database_fname',
                              type=argparse.FileType('r'), nargs='+',
                              help='The name(s) of the regulatory feature databases (FEATHER of LEGACY).')
    parser_motifs.add_argument('-n','--nomenclature',
                               type=str, default='HGNC',
                               help='The nomenclature used for the gene signatures.')
    add_recovery_parameters(parser_motifs)
    add_annotation_parameters(parser_motifs)
    add_computation_parameters(parser_motifs)
    parser_motifs.set_defaults(func=find_motifs)

    # create the parser for the "prune" command
    parser_prune = subparsers.add_parser('prune',
                                         help='Prune targets from a co-expression module based on cis-regulatory cues.')
    parser_prune.add_argument('module_fname',
                              type=argparse.FileType('r'),
                              help='The name of the file that contains the co-expression modules (YAML).')
    parser_prune.add_argument('database_fname',
                              type=argparse.FileType('r'), nargs='+',
                              help='The name(s) of the regulatory feature databases (FEATHER of LEGACY).')
    parser_prune.add_argument('-t', '--output_type',
                              choices=['df', 'regulomes'], default='df',
                              help='The type of output to be generated.')
    add_recovery_parameters(parser_prune)
    add_annotation_parameters(parser_prune)
    add_computation_parameters(parser_prune)
    parser_prune.set_defaults(func=prune_targets)

    # create the parser for the "aucell" command
    parser_aucell = subparsers.add_parser('aucell', help='b help')
    parser_aucell.add_argument('expression_mtx_fname',
                            type=argparse.FileType('r'),
                            help='The name of the file that contains the expression matrix (CSV).')
    parser_aucell.add_argument('regulomes_fname',
                          type=argparse.FileType('r'),
                          help='The name of the file that contains the regulomes (YAML).')
    add_recovery_parameters(parser_aucell)
    add_annotation_parameters(parser_aucell)
    add_computation_parameters(parser_aucell)
    parser_aucell.set_defaults(func=aucell)

    return parser


def scenic(argv=None):
    # TODO: Work In Progress
    raise NotImplementedError
    parser = create_argument_parser()
    parser.parse_args(args=argv)


if __name__ == "__main__":
    scenic()