import sys
import json
from pprint import pprint
import numpy as np
from ase.db import connect 
from ase import units
from ase.thermochemistry import HarmonicThermo, IdealGasThermo
from ase.data import atomic_numbers
from ase.data.colors import jmol_colors
from scipy.interpolate import griddata
from matplotlib import ticker
import matplotlib.pyplot as plt
import traceback
from dataclasses import dataclass
from findiff import ForceExtrapolation
from useful_functions import get_vasp_nelect0
from useful_functions import get_fit_from_points

@dataclass
class FreeEnergyDiagram:
    dbnames: list
    refdbname: str
    potential: float
    pH: float

    def __post_init__(self):
        self.results = {}
        # colors for the SI plots
        self.colors = {'CO':'tab:blue', 'COOH':'tab:green', 'CO2':'tab:red'}

    def _get_frequencies(self):
        """Get the frequencies, hard coded, assumed constant for all surfaces."""
        self.frequencies = {}
        self.frequencies['CO'] = [1832.373539, 481.555294, 467.482512, 425.061714, 73.09318, 68.233697]
        self.frequencies['COOH'] = [3579.438347, 1573.100374, 1241.519251, 932.338544, 624.650858, 619.94479, 468.883838, 246.531351, 245.478053, 75.740879, 73.602391, 15.378853]
        self.frequencies['CO2'] = [1846.027301, 1179.574916, 546.070088, 534.269426, 168.712357, 136.301896, 66.113421, 51.400916, 36.766228]

        self.frequencies['COg'] = [ 30.574661, 30.574661, 2102.17 ]
        self.frequencies['CO2g'] =  [ 8.210836, 8.986016, 626.201759, 626.207647, 1303.431414, 2337.103721 ]
        self.frequencies['H2g'] = [ 101.851377, 101.851377, 4357.748816 ]
        self.frequencies['H2Og'] = [ 10, 76.593339, 84.905187, 1599.342801, 3715.405649, 3823.990900 ] 

    def gas_phase_energies(self, references, referencedb):
        """Get the gas phase small molecule reference energies."""
        for row in referencedb.select():
            functional = row.functional
            pw = row.pw
            state = row.states.replace('state_','')
            references.setdefault(functional,{}).setdefault(pw,{}).setdefault(state,{})['energy'] = row.energy
            references.setdefault(functional,{}).setdefault(pw,{}).setdefault(state,{})['atoms'] = row.toatoms()

    def create_reference_dict(self, referencedb, frequencies):
        """ Create reference dictionary with the input
        The references are made similar to how CatMAP requires them to be made
        Eg. for CO2 reduction,
        H2 = 0, CO2 = 0, H2O = 0
        and CO is referenced accoring to the water gas shift reaction

        :param referencedb: Reference database with all gas references
        :type referencedb: ASE database
        """

        references = {}
        self.gas_phase_energies(references, referencedb=referencedb)

        # frequencies = self._get_frequencies()
        frequencies = self.frequencies

        # CatMAP references
        functional = 'RP'
        CO2g_correction = 0.45
        print('********************************************************')
        print('Adding a correction of 0.45 eV to CO2 gas phase energies.')
        print('For more information see: 10.1007/s10562-012-0947-5')
        print('********************************************************')

        # store internal energies as fixed variables
        COg_E = references[functional][500.]['CO']['energy']
        CO2g_E = references[functional][500.]['CO2']['energy'] + CO2g_correction
        H2g_E = references[functional][500.]['H2']['energy']
        H2Og_E = references[functional][500.]['H2O']['energy']

        # Pressure to plot the free energy diagram
        pressure = {'CO2g': 0.2, 'COg': 0.1, 'H2g': 1., 'H2Og': 1.}
        print('Pressure of molecules (in atm):')
        pprint(pressure)

        
        # Corrections to the free energy 
        cmtoeV = 0.00012 
        COg_G = IdealGasThermo(
            cmtoeV * np.array(frequencies['COg']),
            geometry='linear', 
            atoms=references[functional][500.]['CO']['atoms'],
            symmetrynumber=1,
            spin=0,
        ).get_gibbs_energy(298.15, pressure['COg']*101325, verbose=False)
        print(f'CO(g) free energy: {COg_G}')

        CO2g_G = IdealGasThermo(
            cmtoeV * np.array(frequencies['CO2g']),
            geometry='linear', 
            atoms=references[functional][500.]['CO2']['atoms'],
            symmetrynumber=2,
            spin=0,
        ).get_gibbs_energy(298.15, pressure['CO2g']*101325, verbose=False) 
        print(f'CO2(g) free energy: {CO2g_G}')

        H2g_G = IdealGasThermo(
            cmtoeV * np.array(frequencies['H2g']),
            geometry='linear', 
            atoms=references[functional][500.]['H2']['atoms'],
            symmetrynumber=2,
            spin=0,
        ).get_gibbs_energy(298.15, pressure['H2g']*101325, verbose=False)
        print(f'H2(g) free energy: {H2g_G}')

        H2Og_G = IdealGasThermo(
            cmtoeV * np.array(frequencies['H2Og']),
            geometry='nonlinear', 
            atoms=references[functional][500.]['H2O']['atoms'],
            symmetrynumber=2,
            spin=0,
        ).get_gibbs_energy(298.15, pressure['H2Og']*101325, verbose=False)
        print(f'H2O(g) free energy: {H2Og_G}')

        # Internal energy references
        reference_energies_E = {}
        reference_energies_E['CO'] = CO2g_E + H2g_E - H2Og_E 
        reference_energies_E['COOH'] = CO2g_E + 0.5 * H2g_E 
        reference_energies_E['CO2'] =  CO2g_E
        reference_energies_E['CO(g)'] = CO2g_E + H2g_E - COg_E - H2Og_E

        # The internal energies of the gas reference energies need for CatMAP input
        gas_dict = {'CO2'   : 0.0,
                    'CO'    : -1 * reference_energies_E['CO(g)'],
                    'H2'    : 0.0,
                    'H2O'   : 0.0
                    }

        # Prepare to write out the gas phase energies
        writeout_gas = []
        for gas_name, gas_E in gas_dict.items():
            writ = ['None', 'gas', gas_name, round(gas_E, 3),  frequencies[gas_name+'g'], 'sv_calc']
            writeout_gas.append(writ)
        
        # Free energy references
        reference_energies = {}
        reference_energies['CO'] = CO2g_G - H2Og_G + H2g_G
        reference_energies['COOH'] = CO2g_G + 0.5 * H2g_G 
        reference_energies['CO2'] = CO2g_G
        reference_energies['CO(g)'] = CO2g_G + H2g_G - H2Og_G - COg_G
        

        return reference_energies, reference_energies_E, writeout_gas


    def get_explicit_charge(self,vibresults, atomsIS, atomsFS, 
                fields_to_choose=[0.1, 0.2], displacement=0.01, \
                direction='p'):

        method = ForceExtrapolation(
                vibresults=vibresults,
                fields_to_choose=fields_to_choose,
                displacement=displacement,
                direction=direction,
                atomsIS=atomsIS,
                atomsFS=atomsFS,
                )
        
        method.get_dFdG()
        method.get_dmudR()
        method.get_q()

        return method.q[0]

    def main(self):
        for dbname in self.dbnames:
            self._parse(connect(dbname))

        # store all results
        results = self.results
        diagram = {}
        explicit_charge = {}
        E0 = {}

        # All frequencies 
        self._get_frequencies()
        
        # Create reference dictionary
        references, references_E, writeout_gas = self.create_reference_dict(\
                                    connect(self.refdbname), \
                                    self.frequencies,\
                                    )
        # Electronic energy references that can be directly subtracted
        # for adsorbate species

        # Each species must move down by the CHE dictated energy with potential and pH
        U_RHE = self.potential + 0.059 * self.pH
        CHE_correction = {'CO':2 * U_RHE, 'COOH':U_RHE}
        
        # Dict stores the relevant quantities
        self.diagram = {} # all energy data
        self.explicit_charge = {} # data from finite difference approach
        self.E0 = {} # Energy with no charge corrections

        # prepare the writeout for the CatMAP input file
        writeout = []
        writeout_header = ['surface_name', 'site_name', 'species_name', 'formation_energy', 'frequencies', 'reference']
        writeout.append(writeout_header)
        writeout += writeout_gas

        # Have another writeout for the case where there is no surface charge
        writeout_zero = []
        writeout_zero.append(writeout_header)
        writeout_zero += writeout_gas

        for facet in results:
            for metal in results[facet]:
                if metal in ['Ni', 'Al'] and facet == '111': 
                    # Will not be included in the figure because they are 
                    # not completed datasets or not to be put in scaling (Al)
                    continue
                if metal == 'Fe' and facet == '1_2': 
                    # We skip this structure because there were errors in getting 
                    # the reference structure
                    continue

                # Plotting energies as a function of the total surface charge
                fig, ax = plt.subplots(1, 1, figsize=(6,4), constrained_layout=True)

                # iterate over different states looking at which surface charge component
                # is to be added
                for state in results[facet][metal]:
                    if state == 'slab':
                        continue
                    if 'sp' in state or 'gas' in state or 'dos' in state:
                        continue
                    if state == 'CO2':
                        try:
                            vibresults = results[facet][metal][state]['vacuum'][0.0]['findiff']
                            # get the explicit charge
                            q_eff = self.get_explicit_charge(
                                vibresults=vibresults,
                                atomsFS=results[facet][metal][state]['implicit'][2.00]['atoms'],
                                atomsIS=results[facet][metal]['CO2_gas']['vacuum'][0.0]['atoms'],
                                                    )
                            explicit_charge.setdefault(facet,{})[metal] = q_eff
                        except KeyError as e:
                            continue
                    else:
                        # We assume here that the adsorbates other than CO2 have no explicit 
                        # charge component
                        q_eff = 0
                
                    Eq = []
                    q = []
                    sigma = []

                    # Main block that manages energy vs. surface charge
                    try:
                        for charge in results[facet][metal][state]['implicit']:
                            try:
                                dE = results[facet][metal][state]['implicit'][charge]['energy'] \
                                    - results[facet][metal]['slab']['implicit'][charge]['energy'] \
                                    - references_E[state]
                            except KeyError:
                                continue
                            Eq.append(dE)
                            # correct the charge with the effective charge 
                            # determined by the finite difference method
                            q.append(charge - q_eff/2)
                            area = results[facet][metal]['slab']['vacuum'][0.0]['area'] 
                            sigma = q / area * units._e * 1e6 # mu C / cm-2 
                    except KeyError:
                        continue
                    # get the fit of the energy vs surface charge
                    # There might be some non-linear dependence that comes in sometimes
                    # because of geometry change - we ignore that here because
                    # it is pretty small
                    fit = get_fit_from_points(sigma, Eq, 1) # linear fit of energy to surface charge
                    ax.plot(sigma, Eq, 'o', color=self.colors[state])
                    ax.plot(sigma, fit['p'](sigma), color=self.colors[state])

                    # surface charge corresponding to the requested potential
                    # Assume pzc is the same as wf for now
                    if metal not in ['Fe', 'Ni']:
                        pzc = results[facet][metal]['slab']['vacuum'][0.0]['wf'] - 4.4
                    else:
                        # doped metal changes the wf a lot 
                        pzc = -0.05 # V vs SHE

                    C_gap = 25 # mu F cm-2
                    sigma_for_pot = C_gap * ( self.potential - pzc )

                    vibrations = np.array(self.frequencies[state])

                    # correct for the entropy of the adsorbed molecule using the Harmonic thermodynamic assumption
                    dG_correct = HarmonicThermo(0.00012 * np.array(vibrations)).get_helmholtz_energy(298.15, verbose=False)

                    if 'CO2' in state:
                        # Then we have a relationship with surface charge ready
                        diagram.setdefault(facet,{}).setdefault(metal,{})[state] = fit['p'](-1*sigma_for_pot) + dG_correct - references[state] 
                        writeout.append([metal, facet, state, round(fit['p'](-1*sigma_for_pot),2), vibrations.tolist(), 'sv_calc'])
                        writeout_zero.append([metal, facet, state, round(fit['p'](0),2), vibrations.tolist(), 'sv_calc'])
                        E0.setdefault(facet,{})[metal] = fit['p'](0) + dG_correct  - references[state]
                    else: # in the case of CO and COOH there is no need for explicit charge but there is CHE
                        dE0 = fit['p'](0)
                        dE = fit['p'](-1*sigma_for_pot)
                        diagram.setdefault(facet,{}).setdefault(metal,{})[state] = dE + CHE_correction[state] + dG_correct - references[state] 
                        writeout.append([metal, facet, state, round(dE,2), vibrations.tolist(), 'sv_calc'])
                        writeout_zero.append([metal, facet, state, round(dE0,2), vibrations.tolist(), 'sv_calc'])

                # gas phase references taken into account now
                diagram.setdefault(facet,{}).setdefault(metal,{})['CO2(g)'] = 0
                diagram.setdefault(facet,{}).setdefault(metal,{})['CO(g)'] = -1*references_E['CO(g)'] - references['CO(g)'] + CHE_correction['CO'] 

                # setup the SI plots 
                for i, j in self.colors.items():
                    ax.plot([],[], color=j, label=r''+i.replace('2','$_{2}$'))
                ax.set_ylabel(r'$\Delta E$ / eV')
                ax.set_xlabel(r'$\sigma$ / $\mu C cm^{-2}$')
                ax.legend(loc='best', frameon=False, fontsize=12)

                fig.savefig('output_si/SI_charging_curve_metal_%s_facet_%s.pdf'%(metal, facet))
                plt.close(fig)
        
        # save all the data
        self.diagram = diagram
        self.writeout = writeout
        self.writeout_zero = writeout_zero
        self.explicit_charge = explicit_charge
        self.E0 = E0
        self.references = references
        self.references_E = references_E

    def _parse(self, database):
        """Parse keys from the database

        :param database: database
        :type database: db
        """
        results = self.results
        
        for row in database.select():

            try:
                metal = row.sampling.replace('sampling_','')
                type_of_calc = 'TM'
            except AttributeError:
                metal = row.metal_dopant.replace('metal_dopant_','').replace('_nonorth','')
                type_of_calc = 'SAC'
                # In the case of Fe take only calculations with a U
                if 'Fe' in row.metal_dopant:
                    try:
                        hubbard_U = [a for a in row.data['ldau']['U'] if a > 0.0 ][0]
                    except KeyError:
                        continue
            try:
                displacement = row.displacement
                findiff_calc = True
            except AttributeError:
                findiff_calc = False
            
            state = row.states.replace('state_','').replace('implicit_','')
            implicit = 'implicit' if row.implicit else 'vacuum'
            tot_charge = row.tot_charge
            charge = tot_charge - get_vasp_nelect0(row.toatoms())

            if type_of_calc == 'TM':
                facet = row.facets.replace('facet_','')
            elif type_of_calc == 'SAC':
                facet = row.vacancy_number.replace('vacancy_','') + '_' + row.dopant_number.replace('dopant_','')

            if not findiff_calc:
                try:
                    results.setdefault(facet,{}).setdefault(metal,{}).setdefault(state,{})\
                        .setdefault(implicit,{}).setdefault(charge,{})['energy'] = row.energy
                except AttributeError:
                    continue
                results.setdefault(facet,{}).setdefault(metal,{}).setdefault(state,{})\
                    .setdefault(implicit,{}).setdefault(charge,{})['atoms'] = row.toatoms()
                try:
                    magmom = row.toatoms().get_magnetic_moment()
                except:
                    magmom = 0.0

                results.setdefault(facet,{}).setdefault(metal,{}).setdefault(state,{})\
                    .setdefault(implicit,{}).setdefault(charge,{})['magmom']  = magmom
                
                try:
                    vibrations = row.data.vibrations
                    if len(vibrations) > 0:
                        total_number = len(vibrations)/2
                        vibrations = vibrations[0:int(total_number)]
                        results.setdefault(facet,{}).setdefault(metal,{}).setdefault(state,{})['vibrations'] = vibrations
                except AttributeError:
                    pass

                if implicit == 'vacuum':
                    results.setdefault(facet,{}).setdefault(metal,{}).setdefault(state,{})\
                        .setdefault(implicit,{}).setdefault(charge,{})['dipole'] = row.dipole_field
                    results.setdefault(facet,{}).setdefault(metal,{}).setdefault(state,{})\
                        .setdefault(implicit,{}).setdefault(charge,{})['wf'] = row.wf

                    # get the area by multiplying lattice vectors
                    atoms = row.toatoms()
                    cell = atoms.get_cell()
                    area = atoms.get_volume() / cell[-1, -1] * 1e-16
                    results.setdefault(facet,{}).setdefault(metal,{}).setdefault(state,{})\
                        .setdefault(implicit,{}).setdefault(charge,{})['area'] = area

            if findiff_calc:

                ## now add in details for the finite difference
                findiff = row.findiff
                indices = int(findiff[:-2])
                field = row.field
                direction = findiff[-2]
                displacement = float(displacement.split('_')[-2])
                results.setdefault(facet,{}).setdefault(metal,{}).setdefault(state,{})\
                    .setdefault(implicit,{}).setdefault(charge,{}).setdefault('findiff',{})\
                        .setdefault(indices,{}).setdefault(direction,{})\
                        .setdefault(displacement,{}).setdefault(field,{})['dipole'] = row.dipole_field
                results.setdefault(facet,{}).setdefault(metal,{}).setdefault(state,{})\
                    .setdefault(implicit,{}).setdefault(charge,{}).setdefault('findiff',{})\
                        .setdefault(indices,{}).setdefault(direction,{})\
                        .setdefault(displacement,{}).setdefault(field,{})['forces'] = row.toatoms().get_forces()[indices]

    
def plot_computational_diagram(data, ax, SAC_potential):
    """Create a plot for the computational free energy diagram."""

    all_potentials = []
    ls = ['-', '--', '-.', ':',  'dotted',  'dashed', 'dashdot', 'solid']

    for i, potential in enumerate(data):
        all_potentials.append(float(potential))
        # first the gold plot
        plot_data = [
            data[potential]['100']['Au']['CO2(g)'],
            data[potential]['100']['Au']['CO2'],
            data[potential]['100']['Au']['COOH'],
            data[potential]['100']['Au']['CO'],
            data[potential]['100']['Au']['CO(g)'],
        ]
        fed = [val for val in plot_data for _ in (0, 1)]
        ax[0].plot(fed, color=jmol_colors[atomic_numbers['Au']], lw=2,
                label=r'$%1.1f\mathregular{V}_{\mathregular{SHE}}$'%potential,\
                ls=ls[i])
        
    ax[0].legend(loc='best', frameon=False, \
        fontsize=11,
    )
    
    potential = SAC_potential
    for i, dop in enumerate(range(1,5)):
        try:
            plot_data = [
                data[potential]['2_%s'%dop]['Fe']['CO2(g)'],
                data[potential]['2_%s'%dop]['Fe']['CO2'],
                data[potential]['2_%s'%dop]['Fe']['COOH'],
                data[potential]['2_%s'%dop]['Fe']['CO'],
                data[potential]['2_%s'%dop]['Fe']['CO(g)'],
            ]
            fed = [val for val in plot_data for _ in (0, 1)]
            ax[1].plot(fed, color=jmol_colors[atomic_numbers['Fe']], lw=2,
                    alpha=(i+1) / 4)

            ax[1].annotate(r'FeN$_{%d}$'%dop, xy=(0.8,0.87-0.1*i), alpha=(i+1)/4, \
                        color=jmol_colors[atomic_numbers['Fe']], xycoords='axes fraction', \
                            fontsize=12)
        except KeyError:
            pass

        # and now the NiNC plot
        plot_data = [
            data[potential]['2_%s'%dop]['Ni']['CO2(g)'],
            data[potential]['2_%s'%dop]['Ni']['CO2'],
            data[potential]['2_%s'%dop]['Ni']['COOH'],
            data[potential]['2_%s'%dop]['Ni']['CO'],
            data[potential]['2_%s'%dop]['Ni']['CO(g)'],
        ]
        fed = [val for val in plot_data for _ in (0, 1)]
        ax[2].plot(fed, color=jmol_colors[atomic_numbers['Ni']], lw=2,
                alpha=(i+2) / 5,)
        ax[2].annotate(r'NiN$_{%d}$'%dop, xy=(0.8,0.87-0.1*i), alpha=(i+1)/4, \
                    color=jmol_colors[atomic_numbers['Ni']], xycoords='axes fraction', \
                        fontsize=12)
        

    ax[0].set_xticks([])
    ax[1].set_xticks([])
    ax[2].set_xticks([])
    ax[0].set_ylim([-2.2, 2.2])
    ax[1].set_ylim([-2.2, 2.2])
    ax[2].set_ylim([-2.2, 2.2])

    for i in range(len(ax)):
        ax[i].annotate(r'CO$_2$(g)', xy=(0.05, 0.025), xycoords='axes fraction', fontsize=12)
        ax[i].annotate(r'CO$_2^*$', xy=(0.25, 0.025), xycoords='axes fraction', fontsize=12)
        ax[i].annotate(r'COOH$^*$', xy=(0.4, 0.025), xycoords='axes fraction', fontsize=12)
        ax[i].annotate(r'CO$^*$', xy=(0.65, 0.025), xycoords='axes fraction', fontsize=12)
        ax[i].annotate(r'CO(g)', xy=(0.85, 0.025), xycoords='axes fraction', fontsize=12)
        ax[i].set_ylabel(r'$\Delta G$ / eV')

    ax[0].annotate(r'$\Delta G_{\mathregular{CO}_2^*} > \Delta G_{\mathregular{COOH}^*}$', color='k', 
                xy=(0.72, -1),fontsize=14 )
    ax[1].annotate(r'$\Delta G_{\mathregular{CO}_2^*} > \Delta G_{\mathregular{COOH}^*}$', color='k', 
                xy=(0.72, -1),fontsize=14 )
    ax[2].annotate(r'$\Delta G_{\mathregular{CO}_2^*} < \Delta G_{\mathregular{COOH}^*}$', color='k', 
                xy=(0.72, -1),fontsize=14)
        
def plot_variation_with_potential(explicit_charge):
    """Plot verification plots for the variation of energy with surface charge."""

    fig, ax = plt.subplots(1, 4, figsize=(20,5), sharex=True, sharey=True)
    potential_range = np.linspace(-0.5, 0.5)
    alpha_range = [1, 0.75, 0.5, 0.25]

    for facet in explicit_charge:
        for metal in explicit_charge[facet]:
            fit = np.poly1d([explicit_charge[facet][metal], 0])

            if facet == '100':
                i = 0
                alpha = 0.75
                label = metal + '(100)'
                annotation = 'Transition Metal (100)'
            elif facet == '211':
                i = 1
                alpha = 0.75
                label = metal + '(211)'
                annotation = 'Transition Metal (211)'
            elif '2_' in facet:
                i = 3
                alpha = alpha_range[int(facet.split('_')[-1])-1]
                label = r'$'+metal + 'N_%s'%facet.split('_')[-1] +'$'
                annotation = 'Double Vacancy'
            
            elif '1_' in facet:
                i = 2
                alpha = 1 / int(facet.split('_')[-1])
                label = r'$'+metal + 'N_%s'%facet.split('_')[-1] +'$'
                annotation = 'Single Vacancy'

            ax[i].plot(potential_range, fit(-1*potential_range), '-', lw=3,\
                 color=jmol_colors[atomic_numbers[metal]], alpha=alpha, label=label)
            ax[i].annotate(annotation, xy=(0.4, 0.2), color='k', xycoords='axes fraction')
    
    for a in ax:
        a.set_xlabel(r'$\phi - \phi_{pzc}$ / V ')
        a.legend(loc='best', frameon=False, fontsize=14)

    ax[0].set_ylabel(r'$\Delta E_{CO_{2}^*} - \Delta E_{CO_{2}^*}^0 $ / eV')

    fig.tight_layout()

    return fig, ax


