import uuid
import json

from cyclonedx.factory.license import LicenseFactory
from cyclonedx.model import HashType, HashAlgorithm, OrganizationalEntity, Tool, OrganizationalContact
from cyclonedx.model.bom import Bom
from cyclonedx.model.bom_ref import BomRef
from cyclonedx.model.component import Component, ComponentType, Patch, Pedigree, PatchClassification, LicenseChoice
from cyclonedx.model.issue import IssueType, IssueClassification, IssueTypeSource
from cyclonedx.output import get_instance, OutputFormat

from amendments import _parse_addl_pkg_csv, _get_excld_packages, _filter_excluded_packages
from manifest import VIGILES_TOOL_NAME, VIGILES_TOOL_VENDOR, VIGILES_TOOL_VERSION, DEFAULT_SUPPLIER

BOM_AUTHOR = "vigiles-buildroot"
BOM_VERSION = "1"


def generate_bom_ref():
    return str(uuid.uuid4()).upper()


def generate_bom_refs(pkgs):
    return {pkg: generate_bom_ref() for pkg in pkgs}


def get_bom_ref(bom_refs, pkg):
    bom_ref = bom_refs.get(pkg)
    if not bom_ref:
        # special case for root and additional packages
        bom_ref = generate_bom_ref()
    return bom_ref


def get_dependency_refs(vgls, deps):
    all_deps = deps.get("build", []) + deps.get("runtime", [])
    dep_refs = []
    for dep in all_deps:
        if dep == vgls.get("packages", {}).get("name"):
            continue
        dep_ref = BomRef(value=get_bom_ref(vgls["bom_refs"], dep))
        dep_refs.append(dep_ref)
    return dep_refs


def create_component(vgls, pkg, pkg_dict, additional_pkg=False):
    lc_factory = LicenseFactory()
    name = pkg_dict.get("name", pkg)
    version = pkg_dict.get("version")
    component_type = pkg_dict.get("type", ComponentType.LIBRARY)
    component = Component(
        bom_ref=get_bom_ref(vgls["bom_refs"], name),
        component_type=component_type,
        name=name,
        version=version
    )
    if additional_pkg:
        component.description = "Additional package"

    if pkg_dict.get("license"):
        component.licenses=[LicenseChoice(
            license_=lc_factory.make_from_string(pkg_dict.get("license",""))
        )]
    
    package_supplier = DEFAULT_SUPPLIER
    if pkg_dict.get("package-supplier"):
        package_supplier = pkg_dict.get("package-supplier").replace("Organization:", "").strip()
    
    component.supplier=OrganizationalEntity(
        name=package_supplier
    )

    if pkg_dict.get("checksums"):
        checksum_map = {
            "SHA1": HashAlgorithm.SHA_1,
            "SHA384": HashAlgorithm.SHA_384,
            "SHA512": HashAlgorithm.SHA3_512,
            "SHA256": HashAlgorithm.SHA_256,
            "MD5": HashAlgorithm.MD5,
        }
        hashes = []
        for checksum in pkg_dict.get("checksums"):
            _hash = HashType(
                algorithm = checksum_map.get(checksum.get("algorithm")),
                hash_value=checksum.get("checksum_value")
            )
            hashes.append(_hash)
        component.hashes=hashes

    if pkg_dict.get("cpe-id"):
        cpe = pkg_dict.get("cpe-id") or ""
        if cpe and cpe.lower() != "unknown":
            component.cpe=cpe
    if pkg_dict.get("dependencies"):
        component.dependencies = get_dependency_refs(vgls, pkg_dict.get("dependencies"))

    if pkg_dict.get("patches"):
        patches = set()
        for _patch in pkg_dict.get("patches"):
            patch = Patch(
                type_=PatchClassification.BACKPORT,
                resolves=[IssueType(
                    classification=IssueClassification.SECURITY,
                    source=IssueTypeSource(name=_patch)
                )]
            )
            patches.add(patch)
        component.pedigree = Pedigree(patches=patches)
   
    return component


def create_cyclonedx_sbom(vgls):
    # exclude packages if any
    excld_pkgs = _get_excld_packages(vgls["excld"])
    _filter_excluded_packages(vgls["packages"], excld_pkgs)

    packages = [pkg["name"] for pkg in vgls.get("packages", {}).values()]
    vgls["bom_refs"] = generate_bom_refs(packages)
    
    bom = Bom()
    manifest_name = "-".join([vgls["manifest_name"], "cyclonedx"])
    root_component = create_component(vgls, manifest_name, {
        "type": ComponentType.APPLICATION,
        "version": BOM_VERSION,
    })

    bom.metadata.component = root_component

    bom.metadata.tools = [Tool(
        name=VIGILES_TOOL_NAME,
        version=VIGILES_TOOL_VERSION,
        vendor=VIGILES_TOOL_VENDOR
    )]

    bom.metadata.authors = [
        OrganizationalContact(
            name=BOM_AUTHOR
        )
    ]

    for pkg, info in vgls.get("packages", {}).items():
        component = create_component(vgls, pkg, info)
        bom.components.add(component)

    # include additional packages
    addl_pkg_list = _parse_addl_pkg_csv(vgls["addl"])
    for pkg, version, license in addl_pkg_list:
        component = create_component(vgls, pkg, {
            "version": version,
            "license": license
        }, additional_pkg=True)
        bom.components.add(component)

    del vgls["bom_refs"]
    outputter = get_instance(bom=bom, output_format=OutputFormat.JSON)
    bom_json = outputter.output_as_string()
    return json.loads(bom_json)

