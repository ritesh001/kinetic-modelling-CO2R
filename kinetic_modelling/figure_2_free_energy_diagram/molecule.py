

from ase.data import atomic_numbers
from ase.data.colors import jmol_colors
import collections
from ase.db import connect
import numpy as np
from ase import units
import matplotlib.pyplot as plt
from useful_functions import get_vasp_nelect0
from useful_functions import get_fit_from_points
from ase.thermochemistry import HarmonicThermo

def get_free_energy_harmonic():

    frequencies = {}
    frequencies['CO'] = [1832.373539, 481.555294, 467.482512, 425.061714, 73.09318, 68.233697]
    frequencies['COOH'] = [3579.438347, 1573.100374, 1241.519251, 932.338544, 624.650858, 619.94479, 468.883838, 246.531351, 245.478053, 75.740879, 73.602391, 15.378853]
    frequencies['CO2'] = [1846.027301, 1179.574916, 546.070088, 534.269426, 168.712357, 136.301896, 66.113421, 51.400916, 36.766228]

    harmonic_correction = {}
    for key in frequencies:
        dG_correct = HarmonicThermo(0.00012 * np.array(frequencies[key])).get_helmholtz_energy(298.15, verbose=False)
        harmonic_correction[key] = dG_correct 

    return harmonic_correction

def plot_molecule(potentials, pH, database, ax, references, references_E):
    ## this class will plot the molecular data onto
    ## the requested axis
    figt, axt = plt.subplots(1, 1, figsize=(8,6), constrained_layout=True)
    energy_data = collections.defaultdict(list)
    for row in connect(database).select(sampling='sampling_CoPc'):
        atoms = row.toatoms()
        charge0 = get_vasp_nelect0(atoms)
        q_implicit = row.tot_charge - charge0
        states = row.states
        if 'extrapolation' in row.data:
            if 'dFdG' in row.data['extrapolation']:
                dFdG = row.data['extrapolation']['dFdG']
        try:
            energy = row.energy
        except:
            continue
        cell = atoms.get_cell()
        area = np.linalg.norm(cell[0]) * np.linalg.norm(cell[1]) * 1e-16
        energy_data[states].append([q_implicit, row.energy])
    
    for potential in potentials:

        C_gap = 25 # mu F cm-2
        pzc = -0.05
        sigma_for_pot = C_gap * ( potential - pzc )

        U_RHE = potential + 0.059 * pH
        CHE_correction = {'CO':2 * U_RHE, 'COOH':U_RHE}


        # Add the harmonic correction
        harmonic_correction = get_free_energy_harmonic() 

        # now arrange the energies and order them correctly
        dE_CO2_points = np.sort(energy_data['state_implicit_CO2'])[:,0] - np.sort(energy_data['state_implicit_slab'])[:,0]\
                - references['CO2'] - references_E['CO2'] + harmonic_correction['CO2'] 
        dE_COOH_points = np.sort(energy_data['state_implicit_COOH'])[:,0] - np.sort(energy_data['state_implicit_slab'])[:,0]\
                - references['COOH'] - references_E['COOH'] + CHE_correction['COOH']  + harmonic_correction['COOH']
        dE_CO_points = np.sort(energy_data['state_implicit_CO'])[:,0] - np.sort(energy_data['state_implicit_slab'])[:,0]\
                - references['CO'] - references_E['CO'] + CHE_correction['CO'] + harmonic_correction['CO']
        charges = np.sort(energy_data['state_implicit_CO2'])[:,1]

        surface_charge = -1 * charges / area * units._e * 1e6
        q_co2 = np.sum(dFdG)
        print('-------')
        print('Explicit charge for CO2 in CoPc -%1.2f'%q_co2)
        surface_charge_CO2 = -1 * (charges + q_co2/2) / area * units._e * 1e6
        plot_charge_extrapolated = np.linspace(sigma_for_pot, np.min(surface_charge_CO2), 100)
        axt.axvline(sigma_for_pot)

        dE_CO2 = get_fit_from_points(surface_charge_CO2, dE_CO2_points, 1)['p']
        dE_COOH = get_fit_from_points(surface_charge, dE_COOH_points, 1)['p']
        dE_CO = get_fit_from_points(surface_charge, dE_CO_points, 1)['p']

        axt.plot(surface_charge, dE_CO2_points, 'o', color='tab:red', label='CO2')
        axt.plot(surface_charge, dE_COOH_points, 'o', color='tab:green', label='COOH')
        axt.plot(surface_charge, dE_CO_points, 'o', color='tab:blue', label='CO')
        # Plot to the fitted points
        axt.plot(plot_charge_extrapolated, dE_CO2(plot_charge_extrapolated), color='tab:red')
        axt.plot(plot_charge_extrapolated, dE_COOH(plot_charge_extrapolated), color='tab:green')
        axt.plot(plot_charge_extrapolated, dE_CO(plot_charge_extrapolated), color='tab:blue')

        dE_CO2_g = 0
        dE_CO_g = -1*references_E['CO(g)'] - references['CO(g)'] + CHE_correction['CO']

        energies = [dE_CO2_g, dE_CO2(sigma_for_pot), dE_COOH(sigma_for_pot), dE_CO(sigma_for_pot), dE_CO_g]
        energies = [val for val in energies for _ in (0, 1)]
        ax.plot(energies, color=jmol_colors[atomic_numbers['Co']], lw=2)
    
    ax.annotate(r'CoPc on graphene', xy=(0.4,0.87), alpha=1, \
        color=jmol_colors[atomic_numbers['Co']], xycoords='axes fraction', \
                fontsize=12)
    ax.set_xticks([])
    ax.annotate(r'CO$_2$(g)', xy=(0.05, 0.025), xycoords='axes fraction', fontsize=12)
    ax.annotate(r'CO$_2^*$', xy=(0.25, 0.025), xycoords='axes fraction', fontsize=12)
    ax.annotate(r'COOH$^*$', xy=(0.4, 0.025), xycoords='axes fraction', fontsize=12)
    ax.annotate(r'CO$^*$', xy=(0.65, 0.025), xycoords='axes fraction', fontsize=12)
    ax.annotate(r'CO(g)', xy=(0.85, 0.025), xycoords='axes fraction', fontsize=12)
    ax.set_ylabel(r'$\Delta G$ / eV')
    ax.set_ylim([-2.2,2.2])
    ax.annotate(r'$\Delta G_{\mathregular{CO}_2^*} < \Delta G_{\mathregular{COOH}^*}$', color='k', 
                xy=(0.72, -1),fontsize=14)

    axt.legend(loc='best')
    axt.set_ylabel(r'$\Delta E $ / eV')
    axt.set_xlabel(r'Surface Charge / $\mu C cm^{-2}$')
    figt.savefig('output_si/SI_charging_curve_CoPc.pdf')

