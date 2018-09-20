#!/usr/bin/env python
from __future__ import (absolute_import, division, print_function)
import json
import itertools
import mantid
from mantid.simpleapi import *
import numpy as np
import matplotlib.pyplot as plt
from scipy.constants import m_n, micro, Avogadro
from scipy.constants import physical_constants
from scipy import interpolate, signal, ndimage, optimize

if six.PY3:
    unicode = str
    import configparser
else:
    import ConfigParser as configparser

from inelastic.placzek import calculate_placzek_self_scattering
from inelastic.placzek import get_incident_spectrum_from_monitor, fit_incident_spectrum


# -----------------------------------------------------------------------------------------#
# Utilities

def my_matching_bins(left_workspace, right_workspace):
    left_x_data = mtd[left_workspace].dataX(0)
    right_x_data = mtd[right_workspace].dataX(0)

    if len(left_x_data) != len(right_x_data):
        return False

    if abs(sum(left_x_data) - sum(right_x_data)) > 1.e-7:
        print(
            "Sums do not match: LHS = ",
            sum(left_x_data),
            "RHS =",
            sum(right_x_data))
        return False

    left_delta_x = left_x_data[0] - left_x_data[1]
    right_delta_x = right_x_data[0] - right_x_data[1]

    if abs(left_delta_x -
           right_delta_x) >= 1e-4 or abs(right_x_data[0] -
                                       left_x_data[0]) >= 1e-4:
        return False

    return True


def save_file(ws, title, header=list()):
    with open(title, 'w') as f:
        for line in header:
            f.write('# %s \n' % line)
    SaveAscii(
        InputWorkspace=ws,
        Filename=title,
        Separator='Space',
        ColumnHeader=False,
        AppendToFile=True)


def save_banks_old(ws, title, binning=None):
    CloneWorkspace(InputWorkspace=ws, OutputWorkspace="tmp")
    # if mtd["tmp"].isDistribution():
    #    ConvertFromDistribution(mtd["tmp"])
    if binning:
        Rebin(InputWorkspace="tmp",
              OutputWorkspace="tmp",
              Params=binning,
              PreserveEvents=False)
    if mtd["tmp"].YUnit() == "Counts":
        try:
            print(
                "Unit:",
                mtd["tmp"].YUnit(),
                "Distribution:",
                mtd["tmp"].isDistribution())
            ConvertToDistribution("tmp")
        except BaseException:
            pass
    filename = os.path.join(os.getcwd(), title)
    print(filename)
    SaveAscii(InputWorkspace="tmp",
              Filename=filename,
              Separator='Space',
              ColumnHeader=False,
              AppendToFile=False,
              SpectrumList=range(mtd["tmp"].getNumberHistograms()))
    return


def save_banks(input_workspace, filename, title, output_dir='./', binning=None, grouping_workspace=None):
    CloneWorkspace(InputWorkspace=input_workspace, OutputWorkspace="tmp")
    # if mtd["tmp"].isDistribution():
    #    ConvertFromDistribution(mtd["tmp"])
    if binning:
        Rebin(InputWorkspace="tmp",
              OutputWorkspace="tmp",
              Params=binning,
              PreserveEvents=True)
    if mtd["tmp"].YUnit() == "Counts":
        try:
            print(
                "Unit:",
                mtd["tmp"].YUnit(),
                "Distribution:",
                mtd["tmp"].isDistribution())
            ConvertToDistribution("tmp")
        except BaseException:
            pass
    filename = os.path.join(output_dir, filename)
    if isinstance(mtd["tmp"], mantid.api.IEventWorkspace) and grouping_workspace and mtd["tmp"].YUnit() == "Counts":
        print("Workspace type:", mtd["tmp"].id())
        DiffractionFocussing(InputWorkspace="tmp", OutputWorkspace="tmp",
                             GroupingWorkspace=grouping_workspace,
                             PreserveEvents=False)
    SaveNexusProcessed(
        InputWorkspace="tmp",
        Filename=filename,
        Title=title,
        Append=True,
        PreserveEvents=False,
        WorkspaceIndexList=range(
            mtd["tmp"].getNumberHistograms()))
    return


def save_banks_with_fit(title, fit_range_individual, input_workspace=None, **kwargs):
    # Header
    for i, fitrange in enumerate(fit_range_individual):
        print('fitrange:', fitrange[0], fitrange[1])

        Fit(Function='name=LinearBackground,A0=1.0,A1=0.0',
            WorkspaceIndex=i,
            # range cannot include area with NAN
            StartX=fitrange[0], EndX=fitrange[1],
            InputWorkspace=input_workspace, Output=input_workspace, OutputCompositeMembers=True)
        fit_params = mtd[input_workspace + '_Parameters']

        bank_title = title + '_' + input_workspace + '_bank_' + str(i) + '.dat'
        with open(bank_title, 'w') as f:
            if 'btot_sqrd_avg' in kwargs:
                f.write('#<b^2> : %f \n' % kwargs['btot_sqrd_avg'])
            if 'bcoh_avg_sqrd' in kwargs:
                f.write('#<b>^2 : %f \n' % kwargs['bcoh_avg_sqrd'])
            if 'self_scat' in kwargs:
                f.write('#self scattering : %f \n' % kwargs['self_scat'])
            f.write('#fitrange: %f %f \n' % (fitrange[0], fitrange[1]))
            f.write(
                '#for bank%d: %f + %f * Q\n' %
                (i +
                 1,
                 fit_params.cell(
                     'Value',
                     0),
                 fit_params.cell(
                     'Value',
                     1)))

    # Body
    for bank in range(mtd[input_workspace].getNumberHistograms()):
        x_data = mtd[input_workspace].readX(bank)[0:-1]
        y_data = mtd[input_workspace].readY(bank)
        bank_title = title + '_' + input_workspace + \
                     '_bank_' + str(bank) + '.dat'
        print("####", bank_title)
        with open(bank_title, 'a') as f:
            for x, y in zip(x_data, y_data):
                f.write("%f %f \n" % (x, y))


def generate_croping_table(qmin, qmax):
    mask_info = CreateEmptyTableWorkspace()
    mask_info.addColumn("str", "SpectraList")
    mask_info.addColumn("double", "XMin")
    mask_info.addColumn("double", "XMax")
    for (i, value) in enumerate(qmin):
        mask_info.addRow([str(i), 0.0, value])
    for (i, value) in enumerate(qmax):
        mask_info.addRow([str(i), value, 100.0])

    return mask_info


def get_qmax_from_data(workspace=None, workspace_index=0):
    if workspace is None:
        return None
    return max(mtd[workspace].readX(workspace_index))


# -----------------------------------------------------
# Function to expand string of ints with dashes
# Ex. "1-3, 8-9, 12" -> [1,2,3,8,9,12]

def expand_ints(s):
    spans = (el.partition('-')[::2] for el in s.split(','))
    ranges = (xrange(int(s), int(e) + 1 if e else int(s) + 1)
              for s, e in spans)
    all_nums = itertools.chain.from_iterable(ranges)
    return list(all_nums)


# -------------------------------------------------------------------------
# Function to compress list of ints with dashes
# Ex. [1,2,3,8,9,12] -> 1-3, 8-9, 12


def compress_ints(line_nums):
    seq = []
    final = []
    last = 0

    for index, val in enumerate(line_nums):

        if last + 1 == val or index == 0:
            seq.append(val)
            last = val
        else:
            if len(seq) > 1:
                final.append(str(seq[0]) + '-' + str(seq[len(seq) - 1]))
            else:
                final.append(str(seq[0]))
            seq = []
            seq.append(val)
            last = val

        if index == len(line_nums) - 1:
            if len(seq) > 1:
                final.append(str(seq[0]) + '-' + str(seq[len(seq) - 1]))
            else:
                final.append(str(seq[0]))

    final_str = ', '.join(map(str, final))
    return final_str


# -------------------------------------------------------------------------
# Volume in Beam

class Shape(object):
    def __init__(self):
        self.shape = None

    def getShape(self):
        return self.shape


class Cylinder(Shape):
    def __init__(self):
        self.shape = 'Cylinder'

    def volume(self, radius=None, height=None, **kwargs):
        return np.pi * height * radius * radius


class Sphere(Shape):
    def __init__(self):
        self.shape = 'Sphere'

    def volume(self, radius=None, **kwargs):
        return (4. / 3.) * np.pi * radius * radius * radius


class GeometryFactory(object):

    @staticmethod
    def factory(geometry):
        factory = {"Cylinder": Cylinder(),
                   "Sphere": Sphere()}
        return factory[geometry["Shape"]]


def get_number_atoms(packing_fraction, mass_density, molecular_mass, geometry=None):
    # setup the geometry of the sample
    if geometry is None:
        geometry = dict()
    if "Shape" not in geometry:
        geometry["Shape"] = 'Cylinder'

    # get sample volume in container
    space = GeometryFactory.factory(geometry)
    volume_in_beam = space.volume(**geometry)

    number_density = packing_fraction * mass_density / molecular_mass * Avogadro  # atoms/cm^3
    natoms = number_density * volume_in_beam  # atoms
    return natoms


# -------------------------------------------------------------------------
# Event Filters


def generate_events_filter_from_files(file_names, output_workspace, information_workspace, **kwargs):
    log_name = kwargs.get('LogName', None)
    min_value = kwargs.get('MinimumLogValue', None)
    max_value = kwargs.get('MaximumLogValue', None)
    log_interval = kwargs.get('LogValueInterval', None)
    unit_of_time = kwargs.get('UnitOfTime', 'Nanoseconds')

    # TODO - handle multi-file filtering. Delete this line once implemented.
    assert len(
        file_names) == 1, 'ERROR: Multi-file filtering is not yet supported. (Stay tuned...)'

    for i, filename in enumerate(file_names):
        Load(Filename=filename, OutputWorkspace=filename)
        splitws, infows = GenerateEventsFilter(InputWorkspace=filename,
                                               UnitOfTime=unitOfTime,
                                               LogName=log_name,
                                               MinimumLogValue=min_value,
                                               MaximumLogValue=max_value,
                                               LogValueInterval=log_interval)
        if i == 0:
            GroupWorkspaces(splitws, OutputWorkspace=Outputworkspace)
            GroupWorkspaces(infows, OutputWorkspace=information_workspace)
        else:
            mtd[output_workspace].add(splitws)
            mtd[information_workspace].add(infows)
    return


# -------------------------------------------------------------------------
# Utils 

def print_unit_info(workspace):
    ws = mtd[workspace]
    for i in range(ws.axes()):
        axis = ws.getAxis(i)
        print(
            "Axis {0} is a {1}{2}{3}".format(
                i,
                "Spectrum Axis" if axis.isSpectra() else "",
                "Text Axis" if axis.isText() else "",
                "Numeric Axis" if axis.isNumeric() else ""))

        unit = axis.getUnit()
        print("\n YUnit:{0}".format(ws.YUnit()))
        print("\t caption:{0}".format(unit.caption()))
        print("\t symbol:{0}".format(unit.symbol()))
    return


def set_inelastic_correction(inelastic_dict):
    if inelastic_dict is None:
        inelastic_dict = {"Type": None}
        return inelastic_dict

    corr_type = inelastic_dict["Type"]

    if corr_type == "Placzek":
        default_settings = {"Order": "1st",
                            "Self": True,
                            "Interference": False,
                            "FitSpectrumWith": "GaussConvCubicSpline",
                            "LambdaBinning": "0.16,0.04,2.8"}
        inelastic_settings = default_settings.copy()
        inelastic_settings.update(inelastic_dict)

    else:
        raise Exception("Unknown Inelastic Correction Type")

    return inelastic_settings


# -------------------------------------------------------------------------
# MAIN - NOM_pdf


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Absolute normalization PDF generation")
    parser.add_argument('json', help='Input json file')
    options = parser.parse_args()

    print("loading config from '%s'" % options.json)
    with open(options.json, 'r') as handle:
        config = json.load(handle)
    title = config['Title']
    instr = config['Instrument']

    # Get sample info
    sample = config['Sample']
    sam_mass_density = sample.get('MassDensity', None)
    sam_packing_fraction = sample.get('PackingFraction', None)
    sam_geometry = sample.get('Geometry', None)
    sam_material = sample.get('Material', None)

    # Get normalization info
    van = config['Vanadium']
    van_mass_density = van.get('MassDensity', None)
    van_packing_fraction = van.get('PackingFraction', 1.0)
    van_geometry = van.get('Geometry', None)
    van_material = van.get('Material', 'V')

    # Get calibration, characterization, and other settings
    high_q_linear_fit_range = config['HighQLinearFitRange']
    merging = config['Merging']
    binning = merging['QBinning']
    # workspace indices - zero indexed arrays
    wkspIndices = merging['SumBanks']
    # Grouping
    grouping = merging.get('Grouping', None)
    # TODO how much of each bank gets merged has info here in the form of
    # {"ID", "Qmin", "QMax"}
    cache_dir = config.get("CacheDir", os.path.abspath('.'))
    OutputDir = config.get("OutputDir", os.path.abspath('.'))

    # Create Nexus file basenames
    sample['Runs'] = expand_ints(sample['Runs'])
    sample['Background']['Runs'] = expand_ints(
        sample['Background'].get('Runs', None))

    sam_scans = ','.join(['%s_%d' % (instr, num) for num in sample['Runs']])
    container_scans = ','.join(['%s_%d' % (instr, num)
                                for num in sample['Background']["Runs"]])
    container_bg = None
    if "Background" in sample['Background']:
        sample['Background']['Background']['Runs'] = expand_ints(
            sample['Background']['Background']['Runs'])
        container_bg = ','.join(['%s_%d' % (instr, num)
                                 for num in sample['Background']['Background']['Runs']])
        if len(container_bg) == 0:
            container_bg = None

    van['Runs'] = expand_ints(van['Runs'])
    van_scans = ','.join(['%s_%d' % (instr, num) for num in van['Runs']])

    van_bg_scans = None
    if 'Background' in van:
        van_bg_scans = van['Background']['Runs']
        van_bg_scans = expand_ints(van_bg_scans)
        van_bg_scans = ','.join(['%s_%d' % (instr, num) for num in van_bg_scans])

    # Override Nexus file basename with Filenames if present
    if "Filenames" in sample:
        sam_scans = ','.join(sample["Filenames"])
    if "Filenames" in sample['Background']:
        container_scans = ','.join(sample['Background']["Filenames"])
    if "Background" in sample['Background']:
        if "Filenames" in sample['Background']['Background']:
            container_bg = ','.join(sample['Background']['Background']['Filenames'])
    if "Filenames" in van:
        van_scans = ','.join(van["Filenames"])
    if "Background" in van:
        if "Filenames" in van['Background']:
            van_bg_scans = ','.join(van['Background']["Filenames"])

    # Output nexus filename
    nexus_filename = title + '.nxs'
    try:
        os.remove(nexus_filename)
    except OSError:
        pass

    # Get sample corrections
    sam_geometry = sample.get('Geometry', None)
    sam_abs_corr = sample.get("AbsorptionCorrection", None)
    sam_ms_corr = sample.get("MultipleScatteringCorrection", None)
    sam_inelastic_corr = set_inelastic_correction(
        sample.get('InelasticCorrection', None))

    # Get vanadium corrections
    van_material = van.get('Material', 'V')
    van_mass_density = van.get('MassDensity', van_mass_density)
    van_packing_fraction = van.get(
        'PackingFraction',
        van_packing_fraction)
    van_geometry = van.get('Geometry', None)
    van_abs_corr = van.get("AbsorptionCorrection", {"Type": None})
    van_ms_corr = van.get("MultipleScatteringCorrection", {"Type": None})
    van_inelastic_corr = set_inelastic_correction(
        van.get('InelasticCorrection', None))

    alignAndFocusArgs = dict()
    alignAndFocusArgs['CalFilename'] = config['Calibration']['Filename']
    # alignAndFocusArgs['GroupFilename'] don't use
    # alignAndFocusArgs['Params'] = "0.,0.02,40."
    alignAndFocusArgs['ResampleX'] = -6000
    alignAndFocusArgs['Dspacing'] = True
    alignAndFocusArgs['PreserveEvents'] = True
    alignAndFocusArgs['RemovePromptPulseWidth'] = 50
    alignAndFocusArgs['MaxChunkSize'] = 8
    # alignAndFocusArgs['CompressTolerance'] use defaults
    # alignAndFocusArgs['UnwrapRef'] POWGEN option
    # alignAndFocusArgs['LowResRef'] POWGEN option
    # alignAndFocusArgs['LowResSpectrumOffset'] POWGEN option
    # alignAndFocusArgs['CropWavelengthMin'] from characterizations file
    # alignAndFocusArgs['CropWavelengthMax'] from characterizations file
    alignAndFocusArgs['CacheDir'] = os.path.abspath(cache_dir)
    alignAndFocusArgs['Characterizations'] = 'characterizations'
    alignAndFocusArgs['ReductionProperties'] = '__snspowderreduction'
    results = PDLoadCharacterizations(
        Filename=merging['Characterizations']['Filename'],
        OutputWorkspace='characterizations')

    # Get any additional AlignAndFocusArgs from JSON input
    if "AlignAndFocusArgs" in config:
        otherArgs = config["AlignAndFocusArgs"]
        alignAndFocusArgs.update(otherArgs)

    # Setup grouping
    output_grouping = False
    grp_wksp = "wksp_output_group"

    if grouping:
        if 'Initial' in grouping:
            alignAndFocusArgs['GroupFilename'] = grouping['Initial']
        if 'Output' in grouping:
            output_grouping = True
            LoadDetectorsGroupingFile(InputFile=grouping['Output'],
                                      OutputWorkspace=grp_wksp)
    # If no output grouping specified, create it with Calibration Grouping
    if not output_grouping:
        LoadDiffCal(alignAndFocusArgs['CalFilename'],
                    InstrumentName=instr,
                    WorkspaceName=grp_wksp.replace('_group', ''),
                    MakeGroupingWorkspace=True,
                    MakeCalWorkspace=False,
                    MakeMaskWorkspace=False)
        grp_wksp = None

    # Setup the 6 bank method if no grouping specified
    if not grouping:
        alignAndFocusArgs['PrimaryFlightPath'] = results[2]
        alignAndFocusArgs['SpectrumIDs'] = results[3]
        alignAndFocusArgs['L2'] = results[4]
        alignAndFocusArgs['Polar'] = results[5]
        alignAndFocusArgs['Azimuthal'] = results[6]

    # TODO take out the RecalculatePCharge in the future once tested

    # -----------------------------------------------------------------------------------------#
    # Load Sample
    print("#-----------------------------------#")
    print("# Sample")
    print("#-----------------------------------#")
    AlignAndFocusPowderFromFiles(OutputWorkspace='sample',
                                 Filename=sam_scans,
                                 Absorption=None,
                                 **alignAndFocusArgs)

    sam_wksp = 'sample'
    NormaliseByCurrent(InputWorkspace=sam_wksp,
                       OutputWorkspace=sam_wksp,
                       RecalculatePCharge=True)

    new_sam_geometry = dict()
    for k, v in sam_geometry.items():
        key = str(k)
        if isinstance(v, unicode):
            v = str(v)
        new_sam_geometry[key] = v
    sam_geometry = new_sam_geometry
    sam_geometry.update({'Center': [0., 0., 0., ]})
    sam_material = str(sam_material)
    SetSample(
        InputWorkspace=sam_wksp,
        Geometry=sam_geometry,
        Material={
            'ChemicalFormula': sam_material,
            'SampleMassDensity': sam_mass_density})

    ConvertUnits(InputWorkspace=sam_wksp,
                 OutputWorkspace=sam_wksp,
                 Target="MomentumTransfer",
                 EMode="Elastic")
    sample_title = "sample_and_container"
    print(os.path.join(OutputDir, sample_title + ".dat"))
    print("HERE:", mtd[sam_wksp].getNumberHistograms())
    print(grp_wksp)
    save_banks(input_workspace=sam_wksp,
               filename=nexus_filename,
               title=sample_title,
               output_dir=OutputDir,
               grouping_workspace=grp_wksp,
               binning=binning)

    sam_molecular_mass = mtd[sam_wksp].sample().getMaterial().relativeMolecularMass()
    natoms = get_number_atoms(sam_packing_fraction, sam_mass_density, sam_molecular_mass, geometry=sam_geometry)

    # -----------------------------------------------------------------------------------------#
    # Load Sample Container
    print("#-----------------------------------#")
    print("# Sample Container")
    print("#-----------------------------------#")
    AlignAndFocusPowderFromFiles(OutputWorkspace='container',
                                 Filename=container_scans,
                                 Absorption=None,
                                 **alignAndFocusArgs)

    container = 'container'
    NormaliseByCurrent(InputWorkspace=container,
                       OutputWorkspace=container,
                       RecalculatePCharge=True)
    ConvertUnits(InputWorkspace=container,
                 OutputWorkspace=container,
                 Target="MomentumTransfer",
                 EMode="Elastic")
    container_title = "container"
    save_banks(input_workspace=container,
               filename=nexus_filename,
               title=container_title,
               output_dir=OutputDir,
               grouping_workspace=grp_wksp,
               binning=binning)

    # -----------------------------------------------------------------------------------------#
    # Load Sample Container Background

    if container_bg is not None:
        print("#-----------------------------------#")
        print("# Sample Container's Background")
        print("#-----------------------------------#")
        AlignAndFocusPowderFromFiles(OutputWorkspace='container_background',
                                     Filename=container_bg,
                                     Absorption=None,
                                     **alignAndFocusArgs)

        container_bg = 'container_background'
        NormaliseByCurrent(InputWorkspace=container_bg,
                           OutputWorkspace=container_bg,
                           RecalculatePCharge=True)
        ConvertUnits(InputWorkspace=container_bg,
                     OutputWorkspace=container_bg,
                     Target="MomentumTransfer",
                     EMode="Elastic")
        container_bg_title = "container_background"
        save_banks(input_workspace=container_bg,
                   filename=nexus_filename,
                   title=container_bg_title,
                   output_dir=OutputDir,
                   grouping_workspace=grp_wksp,
                   binning=binning)

    # -----------------------------------------------------------------------------------------#
    # Load Vanadium
    # Load(Filename=van_abs, OutputWorkspace='van_absorption')
    print("#-----------------------------------#")
    print("# Vanadium")
    print("#-----------------------------------#")
    AlignAndFocusPowderFromFiles(OutputWorkspace='vanadium',
                                 Filename=van_scans,
                                 AbsorptionWorkspace=None,
                                 **alignAndFocusArgs)

    van_wksp = 'vanadium'
    if "Shape" not in van_geometry:
        van_geometry.update({'Shape': 'Cylinder'})
    van_geometry.update({'Center': [0., 0., 0., ]})
    NormaliseByCurrent(InputWorkspace=van_wksp,
                       OutputWorkspace=van_wksp,
                       RecalculatePCharge=True)
    new_van_geometry = dict()
    for k, v in van_geometry.items():
        key = str(k)
        if isinstance(v, unicode):
            v = str(v)
        new_van_geometry[key] = v
    van_geometry = new_van_geometry
    van_geometry.update({'Center': [0., 0., 0., ]})
    van_material = str(van_material)

    SetSample(
        InputWorkspace=van_wksp,
        Geometry=van_geometry,
        Material={
            'ChemicalFormula': van_material,
            'SampleMassDensity': van_mass_density})
    ConvertUnits(InputWorkspace=van_wksp,
                 OutputWorkspace=van_wksp,
                 Target="MomentumTransfer",
                 EMode="Elastic")
    vanadium_title = "vanadium_and_background"

    save_banks(input_workspace=van_wksp,
               filename=nexus_filename,
               title=vanadium_title,
               output_dir=OutputDir,
               grouping_workspace=grp_wksp,
               binning=binning)

    van_molecular_mass = mtd[van_wksp].sample().getMaterial().relativeMolecularMass()
    nvan_atoms = get_number_atoms(1.0, van_mass_density, van_molecular_mass, geometry=van_geometry)

    print("Sample natoms:", natoms)
    print("Vanadium natoms:", nvan_atoms)
    print("Vanadium natoms / Sample natoms:", nvan_atoms / natoms)
    # -----------------------------------------------------------------------------------------#
    # Load Vanadium Background
    van_bg = None
    if van_bg_scans is not None:
        print("#-----------------------------------#")
        print("# Vanadium Background")
        print("#-----------------------------------#")
        AlignAndFocusPowderFromFiles(OutputWorkspace='vanadium_background',
                                     Filename=van_bg_scans,
                                     AbsorptionWorkspace=None,
                                     **alignAndFocusArgs)

        van_bg = 'vanadium_background'
        NormaliseByCurrent(InputWorkspace=van_bg,
                           OutputWorkspace=van_bg,
                           RecalculatePCharge=True)
        ConvertUnits(InputWorkspace=van_bg,
                     OutputWorkspace=van_bg,
                     Target="MomentumTransfer",
                     EMode="Elastic")
        vanadium_bg_title = "vanadium_background"
        save_banks(input_workspace=van_bg,
                   filename=nexus_filename,
                   title=vanadium_bg_title,
                   output_dir=OutputDir,
                   grouping_workspace=grp_wksp,
                   binning=binning)

    # -----------------------------------------------------------------------------------------#
    # Load Instrument Characterizations
    PDDetermineCharacterizations(InputWorkspace=sam_wksp,
                                 Characterizations='characterizations',
                                 ReductionProperties='__snspowderreduction')
    propMan = PropertyManagerDataService.retrieve('__snspowderreduction')
    qmax = 2. * np.pi / propMan['d_min'].value
    qmin = 2. * np.pi / propMan['d_max'].value
    for a, b in zip(qmin, qmax):
        print('Qrange:', a, b)
    mask_info = generate_croping_table(qmin, qmax)

    # -----------------------------------------------------------------------------------------#
    # STEP 1: Subtract Backgrounds

    sam_raw = 'sam_raw'
    CloneWorkspace(
        InputWorkspace=sam_wksp,
        OutputWorkspace=sam_raw)  # for later

    container_raw = 'container_raw'
    CloneWorkspace(InputWorkspace=container,
                   OutputWorkspace=container_raw)  # for later

    if van_bg is not None:
        Minus(
            LHSWorkspace=van_wksp,
            RHSWorkspace=van_bg,
            OutputWorkspace=van_wksp)
    Minus(
        LHSWorkspace=sam_wksp,
        RHSWorkspace=container,
        OutputWorkspace=sam_wksp)
    if container_bg is not None:
        Minus(
            LHSWorkspace=container,
            RHSWorkspace=container_bg,
            OutputWorkspace=container)

    for wksp in [container, van_wksp, sam_wksp]:
        ConvertUnits(InputWorkspace=wksp,
                     OutputWorkspace=wksp,
                     Target="MomentumTransfer",
                     EMode="Elastic")
    container_title = "container_minus_back"
    vanadium_title = "vanadium_minus_back"
    sample_title = "sample_minus_back"
    save_banks(input_workspace=container,
               filename=nexus_filename,
               title=container_title,
               output_dir=OutputDir,
               grouping_workspace=grp_wksp,
               binning=binning)
    save_banks(input_workspace=van_wksp,
               filename=nexus_filename,
               title=vanadium_title,
               output_dir=OutputDir,
               grouping_workspace=grp_wksp,
               binning=binning)
    save_banks(input_workspace=sam_wksp,
               filename=nexus_filename,
               title=sample_title,
               output_dir=OutputDir,
               grouping_workspace=grp_wksp,
               binning=binning)

    # -----------------------------------------------------------------------------------------#
    # STEP 2.0: Prepare vanadium as normalization calibrant

    # Multiple-Scattering and Absorption (Steps 2-4) for Vanadium

    van_corrected = 'van_corrected'
    ConvertUnits(InputWorkspace=van_wksp,
                 OutputWorkspace=van_corrected,
                 Target="Wavelength",
                 EMode="Elastic")

    if "Type" in van_abs_corr:
        if van_abs_corr['Type'] == 'Carpenter' or van_ms_corr['Type'] == 'Carpenter':
            MultipleScatteringCylinderAbsorption(
                InputWorkspace=van_corrected,
                OutputWorkspace=van_corrected,
                CylinderSampleRadius=van['Geometry']['Radius'])
        elif van_abs_corr['Type'] == 'Mayers' or van_ms_corr['Type'] == 'Mayers':
            if van_ms_corr['Type'] == 'Mayers':
                MayersSampleCorrection(InputWorkspace=van_corrected,
                                       OutputWorkspace=van_corrected,
                                       MultipleScattering=True)
            else:
                MayersSampleCorrection(InputWorkspace=van_corrected,
                                       OutputWorkspace=van_corrected,
                                       MultipleScattering=False)
        else:
            print("NO VANADIUM absorption or multiple scattering!")
    else:
        CloneWorkspace(
            InputWorkspace=van_corrected,
            OutputWorkspace=van_corrected)

    ConvertUnits(InputWorkspace=van_corrected,
                 OutputWorkspace=van_corrected,
                 Target='MomentumTransfer',
                 EMode='Elastic')
    vanadium_title += "_ms_abs_corrected"
    save_banks(input_workspace=van_corrected,
               filename=nexus_filename,
               title=vanadium_title,
               output_dir=OutputDir,
               grouping_workspace=grp_wksp,
               binning=binning)
    save_banks(input_workspace=van_corrected,
               filename=nexus_filename,
               title=vanadium_title + "_with_peaks",
               output_dir=OutputDir,
               grouping_workspace=grp_wksp,
               binning=binning)

    # TODO subtract self-scattering of vanadium (According to Eq. 7 of Howe,
    # McGreevey, and Howells, JPCM, 1989)

    # Smooth Vanadium (strip peaks plus smooth)

    ConvertUnits(InputWorkspace=van_corrected,
                 OutputWorkspace=van_corrected,
                 Target='dSpacing',
                 EMode='Elastic')
    # After StripVanadiumPeaks, the workspace goes from EventWorkspace -> Workspace2D 
    StripVanadiumPeaks(InputWorkspace=van_corrected,
                       OutputWorkspace=van_corrected,
                       BackgroundType='Quadratic')
    ConvertUnits(InputWorkspace=van_corrected,
                 OutputWorkspace=van_corrected,
                 Target='MomentumTransfer',
                 EMode='Elastic')
    vanadium_title += '_peaks_stripped'
    save_banks(input_workspace=van_corrected,
               filename=nexus_filename,
               title=vanadium_title,
               output_dir=OutputDir,
               grouping_workspace=grp_wksp,
               binning=binning)

    ConvertUnits(InputWorkspace=van_corrected,
                 OutputWorkspace=van_corrected,
                 Target='TOF',
                 EMode='Elastic')
    FFTSmooth(InputWorkspace=van_corrected,
              OutputWorkspace=van_corrected,
              Filter="Butterworth",
              Params='20,2',
              IgnoreXBins=True,
              AllSpectra=True)
    ConvertUnits(InputWorkspace=van_corrected,
                 OutputWorkspace=van_corrected,
                 Target='MomentumTransfer',
                 EMode='Elastic')
    vanadium_title += '_smoothed'
    save_banks(input_workspace=van_corrected,
               filename=nexus_filename,
               title=vanadium_title,
               output_dir=OutputDir,
               grouping_workspace=grp_wksp,
               binning=binning)

    # Inelastic correction
    if van_inelastic_corr['Type'] == "Placzek":
        van_scan = van['Runs'][0]
        van_incident_wksp = 'van_incident_wksp'
        lambda_binning_fit = van['InelasticCorrection']['LambdaBinningForFit']
        lambda_binning_calc = van['InelasticCorrection']['LambdaBinningForCalc']
        print('van_scan:', van_scan)
        get_incident_spectrum_from_monitor(
            '%s_%s' %
            (instr, str(van_scan)), output_workspace=van_incident_wksp)

        fit_type = van['InelasticCorrection']['FitSpectrumWith']
        fit_incident_spectrum(input_workspace=van_incident_wksp,
                              output_workspace=van_incident_wksp,
                              fit_spectrum_with=fit_type,
                              binning_for_fit=lambda_binning_fit,
                              binning_for_calc=lambda_binning_calc,
                              plot_diagnostics=False)

        van_placzek = 'van_placzek'

        SetSample(InputWorkspace=van_incident_wksp,
                  Material={'ChemicalFormula': van_material,
                            'SampleMassDensity': van_mass_density})
        calculate_placzek_self_scattering(incident_workspace=van_incident_wksp,
                                          parent_workspace=van_corrected,
                                          output_workspace=van_placzek,
                                          L1=19.5,
                                          L2=alignAndFocusArgs['L2'],
                                          polar=alignAndFocusArgs['Polar'])
        ConvertToHistogram(InputWorkspace=van_placzek,
                           OutputWorkspace=van_placzek)

        # Save before rebin in Q
        for wksp in [van_placzek, van_corrected]:
            ConvertUnits(InputWorkspace=wksp,
                         OutputWorkspace=wksp,
                         Target='MomentumTransfer',
                         EMode='Elastic')
            Rebin(InputWorkspace=wksp, OutputWorkspace=wksp,
                  Params=binning, PreserveEvents=True)

        save_banks(input_workspace=van_placzek,
                   filename=nexus_filename,
                   title="vanadium_placzek",
                   output_dir=OutputDir,
                   grouping_workspace=grp_wksp,
                   binning=binning)

        # Rebin in Wavelength
        for wksp in [van_placzek, van_corrected]:
            ConvertUnits(InputWorkspace=wksp,
                         OutputWorkspace=wksp,
                         Target='Wavelength',
                         EMode='Elastic')
            Rebin(InputWorkspace=wksp, OutputWorkspace=wksp,
                  Params=lambda_binning_calc, PreserveEvents=True)

        # Save after rebin in Q
        for wksp in [van_placzek, van_corrected]:
            ConvertUnits(InputWorkspace=wksp,
                         OutputWorkspace=wksp,
                         Target='MomentumTransfer',
                         EMode='Elastic')

        # Subtract correction in Wavelength
        for wksp in [van_placzek, van_corrected]:
            ConvertUnits(InputWorkspace=wksp,
                         OutputWorkspace=wksp,
                         Target='Wavelength',
                         EMode='Elastic')
            if not mtd[wksp].isDistribution():
                ConvertToDistribution(wksp)

        Minus(LHSWorkspace=van_corrected,
              RHSWorkspace=van_placzek,
              OutputWorkspace=van_corrected)

        # Save after subtraction
        for wksp in [van_placzek, van_corrected]:
            ConvertUnits(InputWorkspace=wksp,
                         OutputWorkspace=wksp,
                         Target='MomentumTransfer',
                         EMode='Elastic')
        vanadium_title += '_placzek_corrected'
        save_banks(input_workspace=van_corrected,
                   filename=nexus_filename,
                   title=vanadium_title,
                   output_dir=OutputDir,
                   grouping_workspace=grp_wksp,
                   binning=binning)

    ConvertUnits(InputWorkspace=van_corrected,
                 OutputWorkspace=van_corrected,
                 Target='MomentumTransfer',
                 EMode='Elastic')

    SetUncertainties(InputWorkspace=van_corrected,
                     OutputWorkspace=van_corrected,
                     SetError='zero')

    # -----------------------------------------------------------------------------------------#
    # STEP 2.1: Normalize by Vanadium

    for name in [sam_wksp, van_corrected]:
        ConvertUnits(
            InputWorkspace=name,
            OutputWorkspace=name,
            Target='MomentumTransfer',
            EMode='Elastic',
            ConvertFromPointData=False)
        Rebin(InputWorkspace=name, OutputWorkspace=name,
              Params=binning, PreserveEvents=True)
        # if not mtd[name].isDistribution():
        #    ConvertToDistribution(name)

    Divide(
        LHSWorkspace=sam_wksp,
        RHSWorkspace=van_corrected,
        OutputWorkspace=sam_wksp)
    Divide(
        LHSWorkspace=sam_raw,
        RHSWorkspace=van_corrected,
        OutputWorkspace=sam_raw)

    sample_title += "_normalized"
    save_banks(input_workspace=sam_wksp,
               filename=nexus_filename,
               title=sample_title,
               output_dir=OutputDir,
               grouping_workspace=grp_wksp,
               binning=binning)
    save_banks(input_workspace=sam_raw,
               filename=nexus_filename,
               title="sample_normalized",
               output_dir=OutputDir,
               grouping_workspace=grp_wksp,
               binning=binning)

    for name in [container, van_corrected]:
        ConvertUnits(
            InputWorkspace=name,
            OutputWorkspace=name,
            Target='MomentumTransfer',
            EMode='Elastic',
            ConvertFromPointData=False)
        Rebin(InputWorkspace=name, OutputWorkspace=name,
              Params=binning, PreserveEvents=True)
        # if not mtd[name].isDistribution():
        #    ConvertToDistribution(name)
    print()
    print("## Container ##")
    print("YUnit:", mtd[container].YUnit(), "|", mtd[van_corrected].YUnit())
    print(
        "blocksize:",
        mtd[container].blocksize(),
        mtd[van_corrected].blocksize())
    print("dist:", mtd[container].isDistribution(),
          mtd[van_corrected].isDistribution())
    print("Do bins match?:", my_matching_bins(container, van_corrected))
    print(
        "Distributions?",
        mtd[container].isDistribution(),
        mtd[van_corrected].isDistribution())
    print()

    Divide(
        LHSWorkspace=container,
        RHSWorkspace=van_corrected,
        OutputWorkspace=container)
    Divide(
        LHSWorkspace=container_raw,
        RHSWorkspace=van_corrected,
        OutputWorkspace=container_raw)
    if van_bg is not None:
        Divide(
            LHSWorkspace=van_bg,
            RHSWorkspace=van_corrected,
            OutputWorkspace=van_bg)
    if container_bg is not None:
        Divide(
            LHSWorkspace=container_bg,
            RHSWorkspace=van_corrected,
            OutputWorkspace=container_bg)

    print()
    print("## Container After Divide##")
    print("YUnit:", mtd[container].YUnit(), "|", mtd[van_corrected].YUnit())
    print(
        "blocksize:",
        mtd[container].blocksize(),
        mtd[van_corrected].blocksize())
    print("dist:", mtd[container].isDistribution(),
          mtd[van_corrected].isDistribution())
    print("Do bins match?:", my_matching_bins(container, van_corrected))
    print(
        "Distributions?",
        mtd[container].isDistribution(),
        mtd[van_corrected].isDistribution())
    print()

    container_title += '_normalized'
    save_banks(input_workspace=container,
               filename=nexus_filename,
               title=container_title,
               output_dir=OutputDir,
               grouping_workspace=grp_wksp,
               binning=binning)
    save_banks(input_workspace=container_raw,
               filename=nexus_filename,
               title="container_normalized",
               output_dir=OutputDir,
               grouping_workspace=grp_wksp,
               binning=binning)

    if container_bg is not None:
        container_bg_title += "_normalised"
        save_banks(input_workspace=container_bg,
                   filename=nexus_filename,
                   title=container_bg_title,
                   output_dir=OutputDir,
                   grouping_workspace=grp_wksp,
                   binning=binning)

    if van_bg is not None:
        vanadium_bg_title += "_normalized"
        save_banks(input_workspace=van_bg,
                   filename=nexus_filename,
                   title=vanadium_bg_title,
                   output_dir=OutputDir,
                   grouping_workspace=grp_wksp,
                   binning=binning)

    # -----------------------------------------------------------------------------------------#
    # STEP 3 & 4: Subtract multiple scattering and apply absorption correction

    ConvertUnits(
        InputWorkspace=sam_wksp,
        OutputWorkspace=sam_wksp,
        Target="Wavelength",
        EMode="Elastic")

    sam_corrected = 'sam_corrected'
    if sam_abs_corr:
        if sam_abs_corr['Type'] == 'Carpenter' or sam_ms_corr['Type'] == 'Carpenter':
            MultipleScatteringCylinderAbsorption(
                InputWorkspace=sam_wksp,
                OutputWorkspace=sam_corrected,
                CylinderSampleRadius=sample['Geometry']['Radius'])
        elif sam_abs_corr['Type'] == 'Mayers' or sam_ms_corr['Type'] == 'Mayers':
            if sam_ms_corr['Type'] == 'Mayers':
                MayersSampleCorrection(InputWorkspace=sam_wksp,
                                       OutputWorkspace=sam_corrected,
                                       MultipleScattering=True)
            else:
                MayersSampleCorrection(InputWorkspace=sam_wksp,
                                       OutputWorkspace=sam_corrected,
                                       MultipleScattering=False)
        else:
            print("NO SAMPLE absorption or multiple scattering!")
            CloneWorkspace(
                InputWorkspace=sam_wksp,
                OutputWorkspace=sam_corrected)

        ConvertUnits(
            InputWorkspace=sam_corrected,
            OutputWorkspace=sam_corrected,
            Target='MomentumTransfer',
            EMode='Elastic')
        sample_title += "_ms_abs_corrected"
        save_banks(input_workspace=sam_corrected,
                   filename=nexus_filename,
                   title=sample_title,
                   output_dir=OutputDir,
                   grouping_workspace=grp_wksp,
                   binning=binning)
    else:
        CloneWorkspace(InputWorkspace=sam_wksp, OutputWorkspace=sam_corrected)

    # -----------------------------------------------------------------------------------------#
    # STEP 5: Divide by number of atoms in sample

    mtd[sam_corrected] = (nvan_atoms / natoms) * mtd[sam_corrected]
    ConvertUnits(InputWorkspace=sam_corrected, OutputWorkspace=sam_corrected,
                 Target='MomentumTransfer', EMode='Elastic')
    sample_title += "_norm_by_atoms"
    save_banks(input_workspace=sam_corrected,
               filename=nexus_filename,
               title=sample_title,
               output_dir=OutputDir,
               grouping_workspace=grp_wksp,
               binning=binning)

    # -----------------------------------------------------------------------------------------#
    # STEP 6: Divide by total scattering length squared = total scattering
    # cross-section over 4 * pi
    sigma_v = mtd[van_corrected].sample().getMaterial().totalScatterXSection()
    prefactor = (sigma_v / (4. * np.pi))
    print("Total scattering cross-section of Vanadium:",
          sigma_v, " sigma_v / 4*pi:", prefactor)
    mtd[sam_corrected] = prefactor * mtd[sam_corrected]
    sample_title += '_multiply_by_vanSelfScat'
    save_banks(input_workspace=sam_corrected,
               filename=nexus_filename,
               title=sample_title,
               output_dir=OutputDir,
               grouping_workspace=grp_wksp,
               binning=binning)

    # -----------------------------------------------------------------------------------------#
    # STEP 7: Inelastic correction
    ConvertUnits(InputWorkspace=sam_corrected, OutputWorkspace=sam_corrected,
                 Target='Wavelength', EMode='Elastic')
    if sam_inelastic_corr['Type'] == "Placzek":
        if sam_material is None:
            raise Exception(
                "ERROR: For Placzek correction, must specifiy a sample material.")
        for sam_scan in sample['Runs']:
            sam_incident_wksp = 'sam_incident_wksp'
            lambda_binning_fit = sample['InelasticCorrection']['LambdaBinningForFit']
            lambda_binning_calc = sample['InelasticCorrection']['LambdaBinningForCalc']
            get_incident_spectrum_from_monitor(
                '%s_%s' %
                (instr, str(sam_scan)), output_workspace=sam_incident_wksp)

            fit_type = sample['InelasticCorrection']['FitSpectrumWith']
            fit_incident_spectrum(input_workspace=sam_incident_wksp,
                                  output_workspace=sam_incident_wksp,
                                  fit_spectrum_with=fit_type,
                                  binning_for_fit=lambda_binning_fit,
                                  binning_for_calc=lambda_binning_calc)

            sam_placzek = 'sam_placzek'
            SetSample(InputWorkspace=sam_incident_wksp,
                      Material={'ChemicalFormula': sam_material,
                                'SampleMassDensity': sam_mass_density})
            calculate_placzek_self_scattering(incident_workspace=sam_incident_wksp,
                                              parent_workspace=sam_corrected,
                                              output_workspace=sam_placzek,
                                              L1=19.5,
                                              L2=alignAndFocusArgs['L2'],
                                              polar=alignAndFocusArgs['Polar'])
            ConvertToHistogram(InputWorkspace=sam_placzek,
                               OutputWorkspace=sam_placzek)

        # Save before rebin in Q
        for wksp in [sam_placzek, sam_corrected]:
            ConvertUnits(InputWorkspace=wksp,
                         OutputWorkspace=wksp,
                         Target='MomentumTransfer',
                         EMode='Elastic')
            Rebin(InputWorkspace=wksp, OutputWorkspace=wksp,
                  Params=binning, PreserveEvents=True)

        save_banks(input_workspace=sam_placzek,
                   filename=nexus_filename,
                   title="sample_placzek",
                   output_dir=OutputDir,
                   grouping_workspace=grp_wksp,
                   binning=binning)

        # Save after rebin in Q
        for wksp in [sam_placzek, sam_corrected]:
            ConvertUnits(InputWorkspace=wksp,
                         OutputWorkspace=wksp,
                         Target='MomentumTransfer',
                         EMode='Elastic')

        Minus(LHSWorkspace=sam_corrected,
              RHSWorkspace=sam_placzek,
              OutputWorkspace=sam_corrected)

        # Save after subtraction
        for wksp in [sam_placzek, sam_corrected]:
            ConvertUnits(InputWorkspace=wksp,
                         OutputWorkspace=wksp,
                         Target='MomentumTransfer',
                         EMode='Elastic')
        sample_title += '_placzek_corrected'
        save_banks(input_workspace=sam_corrected,
                   filename=nexus_filename,
                   title=sample_title,
                   output_dir=OutputDir,
                   grouping_workspace=grp_wksp,
                   binning=binning)

    # -----------------------------------------------------------------------------------------#
    # STEP 7: Output spectrum

    # TODO Since we already went from Event -> 2D workspace, can't use this anymore
    print('sam:', mtd[sam_corrected].id())
    print('van:', mtd[van_corrected].id())
    if alignAndFocusArgs['PreserveEvents']:
        CompressEvents(InputWorkspace=sam_corrected, OutputWorkspace=sam_corrected)
        # van_corrected is a Workspace2D since we had to use StripVanadiumPeaks
        # CompressEvents(InputWorkspace=van_corrected, OutputWorkspace=van_corrected)

    # -----------------------------------------------------------------------------------------#

    # F(Q) bank-by-bank Section
    CloneWorkspace(InputWorkspace=sam_corrected, OutputWorkspace='FQ_banks_ws')
    FQ_banks = 'FQ_banks'

    # S(Q) bank-by-bank Section
    material = mtd[sam_corrected].sample().getMaterial()
    if material.name() is None or len(material.name().strip()) == 0:
        raise RuntimeError('Sample material was not set')
    bcoh_avg_sqrd = material.cohScatterLength() * material.cohScatterLength()
    btot_sqrd_avg = material.totalScatterLengthSqrd()
    laue_monotonic_diffuse_scat = btot_sqrd_avg / bcoh_avg_sqrd
    CloneWorkspace(InputWorkspace=sam_corrected, OutputWorkspace='SQ_banks_ws')
    SQ_banks = (1. / bcoh_avg_sqrd) * \
               mtd['SQ_banks_ws'] - laue_monotonic_diffuse_scat + 1.

    save_banks(input_workspace="FQ_banks_ws",
               filename=nexus_filename,
               title="FQ_banks",
               output_dir=OutputDir,
               grouping_workspace=grp_wksp,
               binning=binning)
    save_banks(input_workspace="SQ_banks_ws",
               filename=nexus_filename,
               title="SQ_banks",
               output_dir=OutputDir,
               grouping_workspace=grp_wksp,
               binning=binning)

    # -----------------------------------------------------------------------------------------#
    # STOP HERE FOR NOW
    print("<b>^2:", bcoh_avg_sqrd)
    print("<b^2>:", btot_sqrd_avg)
    print("Laue term:", laue_monotonic_diffuse_scat)
    print("sample total xsection:", mtd[sam_corrected].sample().getMaterial().totalScatterXSection())
    print("vanadium total xsection:", mtd[van_corrected].sample().getMaterial().totalScatterXSection())

    # Output Bragg Diffraction
    ConvertUnits(InputWorkspace=sam_corrected,
                 OutputWorkspace=sam_corrected,
                 Target="TOF",
                 EMode="Elastic")

    ConvertToHistogram(InputWorkspace=sam_corrected,
                       OutputWorkspace=sam_corrected)

    Rebin(InputWorkspace=sam_corrected,
          OutputWorkspace=sam_corrected,
          Params="350.0,-0.0001,26233.0")
    xmin = "449.0,719.0,705.0,1137.0,1246.0,350.0"
    xmax = "19492.0,19521.0,21992.0,18920.0,15555.0,26233.0"
    CropWorkspaceRagged(InputWorkspace=sam_corrected,
                        OutputWorkspace=sam_corrected,
                        Xmin=xmin,
                        Xmax=xmax)
    ResampleX(InputWorkspace=sam_corrected,
              OutputWorkspace=sam_corrected,
              NumberBins=3000,
              LogBinning=True)

    SaveGSS(InputWorkspace=sam_corrected,
            Filename=os.path.join(OutputDir, title + ".gsa"),
            SplitFiles=False,
            Append=False,
            MultiplyByBinWidth=True,
            Format="SLOG",
            ExtendedHeader=True)
    # process the run
    '''
    SNSPowderReduction(
        Filename=sam_scans,
        MaxChunkSize=alignAndFocusArgs['MaxChunkSize'],
        PreserveEvents=True,
        PushDataPositive='ResetToZero',
        CalibrationFile=alignAndFocusArgs['CalFilename'],
        CharacterizationRunsFile=merging['Characterizations']['Filename'],
        BackgroundNumber=sample["Background"]["Runs"],
        VanadiumNumber=van["Runs"],
        VanadiumBackgroundNumber=van["Background"]["Runs"],
        RemovePromptPulseWidth=alignAndFocusArgs['RemovePromptPulseWidth'],
        ResampleX=alignAndFocusArgs['ResampleX'],
        BinInDspace=True,
        FilterBadPulses=25.,
        SaveAs="gsas fullprof topas",
        OutputFilePrefix=title,
        OutputDirectory=OutputDir,
        StripVanadiumPeaks=True,
        VanadiumRadius=van_geometry['Radius'],
        NormalizeByCurrent=True,
        FinalDataUnits="dSpacing")

    #-----------------------------------------------------------------------------------------#
    # Ouput bank-by-bank with linear fits for high-Q

    # fit the last 80% of the bank being used
    for i, q in zip(range(mtd[sam_corrected].getNumberHistograms()), qmax):
        qmax_data = getQmaxFromData(sam_corrected, i)
        qmax[i] = q if q <= qmax_data else qmax_data

    fitrange_individual = [(high_q_linear_fit_range*q, q) for q in qmax]

    for q in qmax:
        print('Linear Fit Qrange:', high_q_linear_fit_range*q, q)


    kwargs = { 'btot_sqrd_avg' : btot_sqrd_avg,
               'bcoh_avg_sqrd' : bcoh_avg_sqrd,
               'self_scat' : self_scat }

    save_banks_with_fit( title, fitrange_individual, InputWorkspace='SQ_banks', **kwargs)
    save_banks_with_fit( title, fitrange_individual, InputWorkspace='FQ_banks', **kwargs)
    save_banks_with_fit( title, fitrange_individual, InputWorkspace='FQ_banks_raw', **kwargs)

    save_banks('SQ_banks',     title=os.path.join(OutputDir,title+"_SQ_banks.dat"),     binning=binning)
    save_banks('FQ_banks',     title=os.path.join(OutputDir,title+"_FQ_banks.dat"),     binning=binning)
    save_banks('FQ_banks_raw', title=os.path.join(OutputDir,title+"_FQ_banks_raw.dat"), binning=binning)

    #-----------------------------------------------------------------------------------------#
    # Event workspace -> Histograms
    Rebin(InputWorkspace=sam_corrected, OutputWorkspace=sam_corrected, Params=binning, PreserveEvents=True)
    Rebin(InputWorkspace=van_corrected, OutputWorkspace=van_corrected, Params=binning, PreserveEvents=True)
    Rebin(InputWorkspace='container',   OutputWorkspace='container',   Params=binning, PreserveEvents=True)
    Rebin(InputWorkspace='sample',      OutputWorkspace='sample',      Params=binning, PreserveEvents=True)
    if van_bg is not None:
        Rebin(InputWorkspace=van_bg,        OutputWorkspace='background',      Params=binning, PreserveEvents=True)

    #-----------------------------------------------------------------------------------------#
    # Apply Qmin Qmax limits

    #MaskBinsFromTable(InputWorkspace=sam_corrected, OutputWorkspace='sam_single',       MaskingInformation=mask_info)
    #MaskBinsFromTable(InputWorkspace=van_corrected, OutputWorkspace='van_single',       MaskingInformation=mask_info)
    #MaskBinsFromTable(InputWorkspace='container',   OutputWorkspace='container_single', MaskingInformation=mask_info)
    #MaskBinsFromTable(InputWorkspace='sample',      OutputWorkspace='sample_raw_single',MaskingInformation=mask_info)

    #-----------------------------------------------------------------------------------------#
    # Get sinlge, merged spectrum from banks

    CloneWorkspace(InputWorkspace=sam_corrected, OutputWorkspace='sam_single')
    CloneWorkspace(InputWorkspace=van_corrected, OutputWorkspace='van_single')
    CloneWorkspace(InputWorkspace='container', OutputWorkspace='container_single')
    CloneWorkspace(InputWorkspace='sample', OutputWorkspace='sample_raw_single')
    CloneWorkspace(InputWorkspace='background', OutputWorkspace='background_single')

    SumSpectra(InputWorkspace='sam_single', OutputWorkspace='sam_single',
               ListOfWorkspaceIndices=wkspIndices)
    SumSpectra(InputWorkspace='van_single', OutputWorkspace='van_single',
               ListOfWorkspaceIndices=wkspIndices)

    # Diagnostic workspaces
    SumSpectra(InputWorkspace='container_single', OutputWorkspace='container_single',
               ListOfWorkspaceIndices=wkspIndices)
    SumSpectra(InputWorkspace='sample_raw_single', OutputWorkspace='sample_raw_single',
               ListOfWorkspaceIndices=wkspIndices)
    SumSpectra(InputWorkspace='background_single', OutputWorkspace='background_single',
               ListOfWorkspaceIndices=wkspIndices)

    #-----------------------------------------------------------------------------------------#
    # Merged S(Q) and F(Q)

    save_banks(InputWorkspace="FQ_banks_ws",
               Filename=nexus_filename,
               Title="FQ_banks",
               OutputDir=OutputDir,
               Binning=binning)
    save_banks(InputWorkspace="SQ_banks_ws",
               Filename=nexus_filename,
               Title="SQ_banks",
               OutputDir=OutputDir,
               Binning=binning)


    # do the division correctly and subtract off the material specific term
    CloneWorkspace(InputWorkspace='sam_single', OutputWorkspace='SQ_ws')
    SQ = (1./bcoh_avg_sqrd)*mtd['SQ_ws'] - (term_to_subtract-1.)  # +1 to get back to S(Q)

    CloneWorkspace(InputWorkspace='sam_single', OutputWorkspace='FQ_ws')
    FQ_raw = mtd['FQ_ws']
    FQ = FQ_raw - self_scat

    qmax = 48.0
    Fit(Function='name=LinearBackground,A0=1.0,A1=0.0',
        StartX=high_q_linear_fit_range*qmax, EndX=qmax, # range cannot include area with NAN
        InputWorkspace='SQ', Output='SQ', OutputCompositeMembers=True)
    fitParams = mtd['SQ_Parameters']

    qmax = getQmaxFromData('FQ', WorkspaceIndex=0)
    Fit(Function='name=LinearBackground,A0=1.0,A1=0.0',
        StartX=high_q_linear_fit_range*qmax, EndX=qmax, # range cannot include area with NAN
        InputWorkspace='FQ', Output='FQ', OutputCompositeMembers=True)
    fitParams = mtd['FQ_Parameters']

    qmax = 48.0
    Fit(Function='name=LinearBackground,A0=1.0,A1=0.0',
        StartX=high_q_linear_fit_range*qmax, EndX=qmax, # range cannot include area with NAN
        InputWorkspace='FQ_raw', Output='FQ_raw', OutputCompositeMembers=True)
    fitParams = mtd['FQ_raw_Parameters']

    # Save dat file
    header_lines = ['<b^2> : %f ' % btot_sqrd_avg, \
                    '<b>^2 : %f ' % bcoh_avg_sqrd, \
                    'self scattering: %f ' % self_scat, \
                    'fitrange: %f %f '  % (high_q_linear_fit_range*qmax,qmax), \
                    'for merged banks %s: %f + %f * Q' % (','.join([ str(i) for i in wkspIndices]), \
                                                       fitParams.cell('Value', 0), fitParams.cell('Value', 1)) ]
'''
