"""Module that contains the command line application."""

# Why does this file exist, and why not put this in `__main__`?
#
# You might be tempted to import things from `__main__` later,
# but that will cause problems: the code will get executed twice:
#
# - When you run `python -m variantplaner` python will execute
#   `__main__.py` as a script. That means there won't be any
#   `variantplaner.__main__` in `sys.modules`.
# - When you import `__main__` it will get executed again (as a module) because
#   there's no `variantplaner.__main__` in `sys.modules`.

# std import
from __future__ import annotations

import logging
import os
import pathlib
import sys

# 3rd party import
import click
import polars

# project import
from variantplaner import exception, extract, generate, io, struct


@click.group(name="variantplaner")
@click.option("-t", "--threads", help="Number of threads usable", default=1, type=click.IntRange(0), show_default=True)
@click.option("-v", "--verbose", help="Verbosity level", count=True, type=click.IntRange(0, 4))
def main(threads: int = 1, verbose: int = 0) -> None:
    """Run VariantPlanner."""
    logging.basicConfig(
        style="{",
        format="{asctime} - {name}:{levelname}: {message}",
        encoding="utf-8",
        level=(4 - verbose) * 10,  # Python choose a bad logging levels order
        stream=sys.stderr,
    )

    logger = logging.getLogger("main")

    logger.debug(f"parameter: {threads=} {verbose=}")

    os.environ["POLARS_MAX_THREADS"] = str(threads)


###############
# vcf2parquet #
###############
@main.command()
@click.option(
    "-i",
    "--input-path",
    help="Path to vcf input file",
    type=click.Path(exists=True, dir_okay=False, readable=True, allow_dash=True, path_type=pathlib.Path),
    required=True,
)
@click.option(
    "-v",
    "--variants",
    help="Path where the variants will be written in parquet",
    type=click.Path(dir_okay=False, writable=True, path_type=pathlib.Path),
    required=True,
)
@click.option(
    "-g",
    "--genotypes",
    help="Path where the genotypes will be written in parquet",
    type=click.Path(dir_okay=False, writable=True, path_type=pathlib.Path),
)
@click.option(
    "-a",
    "--annotations",
    help="Path where the annotations will be written in parquet (if no info file is empty)",
    type=click.Path(dir_okay=False, writable=True, path_type=pathlib.Path),
)
@click.option(
    "-f",
    "--format-string",
    help="Value of FORMAT column, line not match with this are ignored",
    type=str,
    default="GT:AD:DP:GQ",
    show_default=True,
)
def vcf2parquet(
    input_path: pathlib.Path,
    variants: pathlib.Path,
    genotypes: pathlib.Path | None,
    annotations: pathlib.Path | None,
    format_string: str = "GT:AD:DP:GQ",
) -> None:
    """Convert a vcf in parquet."""
    logger = logging.getLogger("vcf2parquet")

    logger.debug(f"parameter: {input_path=} {variants=} {genotypes=}")

    try:
        vcf_header = io.vcf.extract_header(input_path)
    except exception.NotAVCFError:
        logger.exception("")
        sys.exit(11)

    # Read vcf and manage structural variant
    lf = io.vcf.into_lazyframe(input_path, extension=io.vcf.IntoLazyFrameExtension.MANAGE_SV)

    extract.variants(lf).sink_parquet(variants)
    logger.info(f"finish write {variants}")

    if genotypes:
        try:
            genotypes_lf = extract.genotypes(lf, io.vcf.format2expr(vcf_header, input_path), format_string)
        except exception.NoGenotypeError:
            logger.exception("")
            sys.exit(12)

        genotypes_lf.sink_parquet(genotypes)

    if annotations:
        annotations_lf = lf.with_columns(io.vcf.info2expr(vcf_header, input_path))
        annotations_lf = annotations_lf.drop(["chr", "pos", "ref", "alt", "filter", "qual", "info"])
        annotations_lf.sink_parquet(annotations)

    logger.info(f"finish write {genotypes}")


###############
# parquet2vcf #
###############
@main.command()
@click.option(
    "-i",
    "--input-path",
    help="Path to variants in parquet format",
    type=click.Path(exists=True, dir_okay=False, readable=True, allow_dash=True, path_type=pathlib.Path),
    required=True,
)
@click.option(
    "-g",
    "--genotypes-path",
    help="Path to genotypes in parquet format",
    type=click.Path(exists=True, dir_okay=False, readable=True, path_type=pathlib.Path),
)
@click.option(
    "-o",
    "--output",
    help="Path where the vcf is write",
    type=click.Path(dir_okay=False, writable=True, path_type=pathlib.Path),
    required=True,
)
@click.option(
    "-c",
    "--chromosome",
    help="Name of chromosome column",
    type=str,
    default="chr",
    show_default=True,
)
@click.option(
    "-p",
    "--position",
    help="Name of position column",
    type=str,
    default="pos",
    show_default=True,
)
@click.option(
    "-I",
    "--identifier",
    help="Name of identity column",
    type=str,
    default="id",
    show_default=True,
)
@click.option(
    "-r",
    "--reference",
    help="Name of reference column",
    default="ref",
    show_default=True,
)
@click.option(
    "-a",
    "--alternative",
    help="Name of alternative column",
    default="alt",
    show_default=True,
)
@click.option(
    "-q",
    "--quality",
    help="Name of quality column",
    type=str,
)
@click.option(
    "-f",
    "--filter",
    "filter_col",
    help="Name of filter column",
    type=str,
)
@click.option(
    "-F",
    "--format",
    "format_str",
    help="Value of format column",
    type=str,
)
def parquet2vcf(
    input_path: pathlib.Path,
    output: pathlib.Path,
    genotypes_path: pathlib.Path | None = None,
    chromosome: str = "chr",
    position: str = "pos",
    identifier: str = "id",
    reference: str = "ref",
    alternative: str = "alt",
    quality: str | None = None,
    filter_col: str | None = None,
    format_str: str | None = None,
) -> None:
    """Convert parquet in vcf."""
    logger = logging.getLogger("parquet2vcf")

    logger.debug(f"parameter: {input_path=} {output=}")

    variants_lf = polars.scan_parquet(input_path)

    if genotypes_path and format_str:
        genotypes_lf = polars.scan_parquet(genotypes_path)
        sample_name = genotypes_lf.select("sample").collect().get_column("sample").to_list()
        merge_lf = extract.merge_variants_genotypes(variants_lf, genotypes_lf, sample_name)
        sample2vcf_col2polars_col: dict[str, dict[str, str]] = {}
        for sample in sample_name:
            sample2vcf_col2polars_col[sample] = {}
            for format_col in format_str.split(":"):
                sample2vcf_col2polars_col[sample][format_col] = f"{sample}_{format_col.lower()}"

        io.vcf.from_lazyframe(
            merge_lf,
            output,
            io.vcf.build_rename_column(
                chromosome,
                position,
                identifier,
                reference,
                alternative,
                quality,
                filter_col,
                [],
                format_str,
                sample2vcf_col2polars_col,
            ),
        )
    else:
        io.vcf.from_lazyframe(
            variants_lf,
            output,
            io.vcf.build_rename_column(
                chromosome,
                position,
                identifier,
                reference,
                alternative,
                quality,
                filter_col,
            ),
        )


##########
# Struct #
##########
@main.group("struct")
@click.pass_context
@click.option(
    "-i",
    "--input-paths",
    help="Paths of the variant files to be merged",
    multiple=True,
    type=click.Path(exists=True, dir_okay=False, readable=True, path_type=pathlib.Path),
    required=True,
)
def struct_main(ctx: click.Context, input_paths: list[pathlib.Path]) -> None:
    """Struct operation on parquet file."""
    logger = logging.getLogger("struct")

    ctx.obj = {"input_paths": input_paths}

    logger.debug(f"parameter: {input_paths=}")


@struct_main.command()
@click.pass_context
@click.option(
    "-o",
    "--output-path",
    help="Path where merged variants will be written",
    type=click.Path(writable=True, path_type=pathlib.Path),
    required=True,
)
@click.option(
    "-b",
    "--bytes-memory-limit",
    help="Number of bytes used to build chunk of merge file",
    type=click.IntRange(0),
    show_default=True,
    default=10_000_000_000,
)
def variants(ctx: click.Context, output_path: pathlib.Path, bytes_memory_limit: int = 10_000_000_000) -> None:
    """Merge multiple variants parquet file in one.

    If you set TMPDIR, TEMP or TMP environment variable you can control where temp file is create.
    """
    logger = logging.getLogger("struct.variants")

    ctx.ensure_object(dict)

    input_paths = ctx.obj["input_paths"]

    logger.debug(f"parameter: {output_path=} {bytes_memory_limit}")

    struct.variants.merge(input_paths, output_path, bytes_memory_limit)


@struct_main.command()
@click.pass_context
@click.option(
    "-p",
    "--prefix-path",
    help="Path prefix",
    type=click.Path(file_okay=False, dir_okay=True, path_type=pathlib.Path),
    required=True,
)
def genotypes(ctx: click.Context, prefix_path: pathlib.Path) -> None:
    """Convert set of genotype parquet in hive file."""
    logger = logging.getLogger("struct.genotypes")

    ctx.ensure_object(dict)

    input_paths = ctx.obj["input_paths"]

    threads = int(os.environ["POLARS_MAX_THREADS"])
    os.environ["POLARS_MAX_THREADS"] = "1"

    logger.debug(f"parameter: {prefix_path=}")

    struct.genotypes.hive(input_paths, prefix_path, threads)


###############
# Annotations #
###############
@main.group("annotations")
@click.pass_context
@click.option(
    "-i",
    "--input-path",
    help="Path to input file",
    type=click.Path(exists=True, dir_okay=False, readable=True, allow_dash=True, path_type=pathlib.Path),
    required=True,
)
@click.option(
    "-o",
    "--output-path",
    help="Path where variants annotations will be written in parquet",
    type=click.Path(writable=True, path_type=pathlib.Path),
    required=True,
)
def annotations_main(ctx: click.Context, input_path: pathlib.Path, output_path: pathlib.Path) -> None:
    """Convert an annotation variation file in parquet."""
    logger = logging.getLogger("annotations")

    ctx.obj = {"input_path": input_path, "output_path": output_path}

    logger.debug(f"parameter: {input_path=} {output_path=}")


@annotations_main.command("vcf")
@click.pass_context
@click.option(
    "-i",
    "--info",
    multiple=True,
    help="List of info fields that are kept if this list is empty all fields are kept only the first vcf file header is read",
    type=str,
)
@click.option(
    "-r",
    "--rename-id",
    help="Set column name of variant id",
    type=str,
)
def annotations_vcf(ctx: click.Context, info: set[str] | None = None, rename_id: str | None = None) -> None:
    """Convert an annotated vcf in parquet."""
    logger = logging.getLogger("annotations-vcf")

    ctx.ensure_object(dict)

    input_path = ctx.obj["input_path"]
    output_path = ctx.obj["output_path"]

    logger.debug(f"parameter: {input_path=} {output_path=} {info=}")

    try:
        vcf_header = io.vcf.extract_header(input_path)
        info_parser = io.vcf.info2expr(vcf_header, input_path, info)
        lf = io.vcf.into_lazyframe(input_path)
    except exception.NotAVCFError:
        logger.exception("")
        sys.exit(21)

    lf = lf.with_columns(info_parser).drop(["chr", "pos", "ref", "alt", "filter", "qual", "info"])

    if rename_id:
        logger.info(f"Rename vcf variant id in {rename_id}")
        lf = lf.rename({"vid": rename_id})

    lf.sink_parquet(output_path, compression="snappy")


@annotations_main.command("csv")
@click.pass_context
@click.option(
    "-c",
    "--chromosome",
    help="Name of chromosome column",
    type=str,
    required=True,
)
@click.option(
    "-p",
    "--position",
    help="Name of position column",
    type=str,
    required=True,
)
@click.option(
    "-r",
    "--reference",
    help="Name of reference column",
    type=str,
    required=True,
)
@click.option(
    "-a",
    "--alternative",
    help="Name of alternative column",
    type=str,
    required=True,
)
@click.option(
    "-i",
    "--info",
    multiple=True,
    help="List of columns that are kept if this list is empty all columns are kept",
    type=str,
)
@click.option(
    "-s",
    "--separator",
    help="Single byte character to use as delimiter in the file",
    type=str,
    default=",",
    show_default=True,
)
def annotations_csv(
    ctx: click.Context,
    chromosome: str,
    position: str,
    reference: str,
    alternative: str,
    info: list[str],
    separator: str = ",",
) -> None:
    """Convert an annotated csv in parquet."""
    logger = logging.getLogger("annotations-vcf")

    ctx.ensure_object(dict)

    input_path = ctx.obj["input_path"]
    output_path = ctx.obj["output_path"]

    logger.debug(
        f"parameter: {input_path=} {output_path=} {chromosome=} {position=} {reference=} {alternative=} {info=}",
    )

    lf = io.csv.into_lazyframe(
        input_path,
        chromosome,
        position,
        reference,
        alternative,
        info,
        separator=separator,
    )

    lf = lf.drop([chromosome, position, reference, alternative])

    lf.collect(streaming=True).write_parquet(output_path)


############
# Metadata #
############
@main.group()
@click.pass_context
@click.option(
    "-i",
    "--input-path",
    help="Path to input file",
    type=click.Path(exists=True, dir_okay=False, readable=True, allow_dash=True, path_type=pathlib.Path),
    required=True,
)
@click.option(
    "-o",
    "--output-path",
    help="Path where variants annotations will be written in parquet",
    type=click.Path(writable=True, path_type=pathlib.Path),
    required=True,
)
def metadata(ctx: click.Context, input_path: pathlib.Path, output_path: pathlib.Path) -> None:
    """Convert an metadata file in parquet."""
    logger = logging.getLogger("metadata")

    ctx.obj = {"input_path": input_path, "output_path": output_path}

    logger.debug(f"parameter: {input_path=} {output_path=}")


@metadata.command("json")
@click.pass_context
@click.option(
    "-f",
    "--fields",
    multiple=True,
    help="List of fields that are kept if this list is empty all fields are kept",
    type=str,
)
def metadata_json(ctx: click.Context, fields: list[str]) -> None:
    """Convert an metadata json in parquet."""
    logger = logging.getLogger("metadata-json")

    ctx.ensure_object(dict)

    input_path = ctx.obj["input_path"]
    output_path = ctx.obj["output_path"]

    logger.debug(f"parameter: {input_path=} {output_path=} {fields=}")

    lf = polars.read_json(input_path)

    if fields:
        lf = lf.select(fields)

    lf.write_parquet(output_path)


@metadata.command("csv")
@click.pass_context
@click.option(
    "-c",
    "--columns",
    multiple=True,
    help="List of columns that are kept if this list is empty all columns are kept",
    type=str,
)
@click.option(
    "-s",
    "--separator",
    help="Single byte character to use as delimiter in the file",
    type=str,
    default=",",
    show_default=True,
)
def metadata_csv(ctx: click.Context, columns: list[str], separator: str = ",") -> None:
    """Convert an metadata csv in parquet."""
    logger = logging.getLogger("metadata-csv")

    ctx.ensure_object(dict)

    input_path = ctx.obj["input_path"]
    output_path = ctx.obj["output_path"]

    logger.debug(f"parameter: {input_path=} {output_path=} {columns=}")

    lf = polars.scan_csv(input_path, separator=separator)

    if columns:
        lf = lf.select(columns)

    lf.sink_parquet(output_path)


############
# Generate #
############
@main.group("generate")
def generate_main() -> None:
    """Subcommand generate thing."""
    logger = logging.getLogger("generate")

    logger.debug("parameter: ")


@generate_main.command("transmission")
@click.option(
    "-i",
    "--input-path",
    help="Path genotypes parquet file",
    type=click.Path(exists=True, dir_okay=False, readable=True, allow_dash=True, path_type=pathlib.Path),
    required=True,
)
@click.option(
    "-p",
    "--ped-path",
    help="Path to the ped file",
    type=click.Path(exists=True, dir_okay=False, readable=True, path_type=pathlib.Path),
)
@click.option(
    "-I",
    "--index",
    help="Sample name of index",
    type=str,
)
@click.option(
    "-m",
    "--mother",
    help="Sample name of mother",
    type=str,
)
@click.option(
    "-f",
    "--father",
    help="Sample name of father",
    type=str,
)
@click.option(
    "-t",
    "--transmission-path",
    help="Path transmission mode will be store",
    type=click.Path(dir_okay=False, writable=True, path_type=pathlib.Path),
)
def transmission(
    input_path: pathlib.Path,
    ped_path: pathlib.Path | None,
    index: str | None,
    mother: str | None,
    father: str | None,
    transmission_path: pathlib.Path,
) -> None:
    """Generate transmission information."""
    logger = logging.getLogger("generate-origin")

    logger.debug(f"parameter: {input_path=} {ped_path=} {index=} {mother=} {father=} {transmission_path=}")

    genotypes_lf = polars.scan_parquet(input_path)

    if ped_path:
        ped_lf = io.ped.into_lazyframe(ped_path)
        transmission_lf = generate.transmission_ped(genotypes_lf, ped_lf)
    elif index and mother and father:
        transmission_lf = generate.transmission(genotypes_lf, index, mother, father)
    else:
        logging.error("You must specify ped file or index, mother and father sample name")
        sys.exit(31)

    transmission_lf.write_parquet(transmission_path)
