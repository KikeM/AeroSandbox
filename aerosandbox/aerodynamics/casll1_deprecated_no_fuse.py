from .aerodynamics import *
from ..geometry import *


class Casll1(AeroProblem):
    # Usage:
    #   # Set up attrib_name problem using the syntax in the AeroProblem constructor (e.g. "Casll1(airplane = attrib_name, op_point = op)" for some Airplane attrib_name and OperatingPoint op)
    #   # Call the setup() method on the vlm3 object to set up the problem in a CasADi Opti environment.
    #   # Solve the problem with opti.solve().
    #   # Access results in the command line, or through properties of the Casll1 class.
    #   #   # In attrib_name future update, this will be done through attrib_name standardized AeroData class.

    def __init__(self,
                 airplane,  # type: Airplane
                 op_point,  # type: op_point
                 opti,  # type: cas.Opti
                 ):
        super().__init__(airplane, op_point)
        self.opti = opti

    def setup(self,
              verbose=True,  # Choose whether or not you want verbose output
              run_symmetric_if_possible=True,
              # Choose whether or not you want to run a symmetric_problem analysis about XZ (~4x faster)
              ):
        # Runs attrib_name point analysis at the specified op-point.
        self.verbose = verbose
        if run_symmetric_if_possible:
            try:
                symmetric_problem = (
                    self.op_point.beta == 0 and
                    self.op_point.p == 0 and
                    self.op_point.r == 0 and
                    self.airplane.is_symmetric()
                )
            except RuntimeError:
                symmetric_problem = False

            if symmetric_problem:
                self.symmetric_problem = True
                if self.verbose: print("Symmetry confirmed; running as symmetric problem...")
            else:
                self.symmetric_problem = False
                if self.verbose: print(
                    "Problem appears to be asymmetric, so a symmetric solve is not possible; running as asymmetric problem...")
        else:
            self.symmetric_problem = False
            if self.verbose: print("Running as asymmetric problem...")

        if self.verbose: print("Setting up casLL1 calculation...")

        self.make_panels()
        self.setup_geometry()
        self.setup_operating_point()
        self.calculate_vortex_strengths()
        self.calculate_forces()

        if self.verbose: print("casLL1 setup complete! Ready to pass into the solver...")

    def make_panels(self):
        # Creates self.panel_coordinates_structured_list and self.wing_mcl_normals.

        if self.verbose: print("Meshing...")

        front_left_vertices = []
        front_right_vertices = []
        back_left_vertices = []
        back_right_vertices = []
        CL_functions = []
        CDp_functions = []
        Cm_functions = []
        wing_id = []

        for wing_num in range(len(self.airplane.wings)):
            # Get the wing
            wing = self.airplane.wings[wing_num]  # type: Wing
            # Make the panels for each section.
            for section_num in range(len(wing.xsecs) - 1):
                # Define the relevant cross sections
                inner_xsec = wing.xsecs[section_num]  # type: WingXSec
                outer_xsec = wing.xsecs[section_num + 1]  # type: WingXSec

                # Find the corners
                inner_xsec_xyz_le = inner_xsec.xyz_le + wing.xyz_le
                inner_xsec_xyz_te = inner_xsec.xyz_te() + wing.xyz_le
                outer_xsec_xyz_le = outer_xsec.xyz_le + wing.xyz_le
                outer_xsec_xyz_te = outer_xsec.xyz_te() + wing.xyz_le

                # Define number of spanwise points
                n_spanwise_coordinates = inner_xsec.spanwise_panels + 1

                # Get the spanwise coordinates
                if inner_xsec.spanwise_spacing == 'uniform':
                    nondim_spanwise_coordinates = np.linspace(0, 1, n_spanwise_coordinates)
                elif inner_xsec.spanwise_spacing == 'cosine':
                    nondim_spanwise_coordinates = np_cosspace(0, 1, n_spanwise_coordinates)
                else:
                    raise Exception("Bad init_val of section.spanwise_spacing!")

                for span_index in range(inner_xsec.spanwise_panels):
                    nondim_spanwise_coordinate = nondim_spanwise_coordinates[span_index]
                    nondim_spanwise_coordinate_next = nondim_spanwise_coordinates[span_index + 1]

                    # Calculate vertices
                    front_left_vertex = (
                            inner_xsec_xyz_le * (1 - nondim_spanwise_coordinate) +
                            outer_xsec_xyz_le * nondim_spanwise_coordinate)
                    front_right_vertex = (
                            inner_xsec_xyz_le * (1 - nondim_spanwise_coordinate_next) +
                            outer_xsec_xyz_le * nondim_spanwise_coordinate_next
                    )
                    back_left_vertex = (
                            inner_xsec_xyz_te * (1 - nondim_spanwise_coordinate) +
                            outer_xsec_xyz_te * nondim_spanwise_coordinate
                    )
                    back_right_vertex = (
                            inner_xsec_xyz_te * (1 - nondim_spanwise_coordinate_next) +
                            outer_xsec_xyz_te * nondim_spanwise_coordinate_next
                    )

                    front_left_vertices.append(front_left_vertex)
                    front_right_vertices.append(front_right_vertex)
                    back_left_vertices.append(back_left_vertex)
                    back_right_vertices.append(back_right_vertex)

                    CL_functions.append(
                        lambda alpha, Re, mach,
                               inner_xsec=inner_xsec,
                               outer_xsec=outer_xsec,
                               nondim_spanwise_coordinate=nondim_spanwise_coordinate,
                        : (
                                inner_xsec.airfoil.CL_function(
                                    alpha=alpha, Re=Re, mach=mach,
                                    deflection=inner_xsec.control_surface_deflection
                                ) * (1 - nondim_spanwise_coordinate) +
                                outer_xsec.airfoil.CL_function(
                                    alpha=alpha, Re=Re, mach=mach,
                                    deflection=inner_xsec.control_surface_deflection
                                ) * nondim_spanwise_coordinate
                        )
                    )
                    CDp_functions.append(
                        lambda alpha, Re, mach,
                               inner_xsec=inner_xsec,
                               outer_xsec=outer_xsec,
                               nondim_spanwise_coordinate=nondim_spanwise_coordinate,
                        : (
                                inner_xsec.airfoil.CDp_function(
                                    alpha=alpha, Re=Re, mach=mach,
                                    deflection=inner_xsec.control_surface_deflection
                                ) * (1 - nondim_spanwise_coordinate) +
                                outer_xsec.airfoil.CDp_function(
                                    alpha=alpha, Re=Re, mach=mach,
                                    deflection=inner_xsec.control_surface_deflection
                                ) * nondim_spanwise_coordinate
                        )
                    )
                    Cm_functions.append(
                        lambda alpha, Re, mach,
                               inner_xsec=inner_xsec,
                               outer_xsec=outer_xsec,
                               nondim_spanwise_coordinate=nondim_spanwise_coordinate,
                        : (
                                inner_xsec.airfoil.Cm_function(
                                    alpha=alpha, Re=Re, mach=mach,
                                    deflection=inner_xsec.control_surface_deflection
                                ) * (1 - nondim_spanwise_coordinate) +
                                outer_xsec.airfoil.Cm_function(
                                    alpha=alpha, Re=Re, mach=mach,
                                    deflection=inner_xsec.control_surface_deflection
                                ) * nondim_spanwise_coordinate
                        )
                    )

                    wing_id.append(wing_num)

                    if wing.symmetric and not self.symmetric_problem:
                        front_right_vertices.append(reflect_over_XZ_plane(front_left_vertex))
                        front_left_vertices.append(reflect_over_XZ_plane(front_right_vertex))
                        back_right_vertices.append(reflect_over_XZ_plane(back_left_vertex))
                        back_left_vertices.append(reflect_over_XZ_plane(back_right_vertex))

                        CL_functions.append(
                            lambda alpha, Re, mach,
                                   inner_xsec=inner_xsec,
                                   outer_xsec=outer_xsec,
                                   nondim_spanwise_coordinate=nondim_spanwise_coordinate,
                            : (
                                    inner_xsec.airfoil.CL_function(
                                        alpha=alpha, Re=Re, mach=mach,
                                        deflection=(-inner_xsec.control_surface_deflection
                                                    if inner_xsec.control_surface_type == "asymmetric" else
                                                    inner_xsec.control_surface_deflection)
                                    ) * (1 - nondim_spanwise_coordinate) +
                                    outer_xsec.airfoil.CL_function(
                                        alpha=alpha, Re=Re, mach=mach,
                                        deflection=(-inner_xsec.control_surface_deflection
                                                    if inner_xsec.control_surface_type == "asymmetric" else
                                                    inner_xsec.control_surface_deflection)
                                    ) * nondim_spanwise_coordinate
                            )
                        )
                        CDp_functions.append(
                            lambda alpha, Re, mach,
                                   inner_xsec=inner_xsec,
                                   outer_xsec=outer_xsec,
                                   nondim_spanwise_coordinate=nondim_spanwise_coordinate,
                            : (
                                    inner_xsec.airfoil.CDp_function(
                                        alpha=alpha, Re=Re, mach=mach,
                                        deflection=(-inner_xsec.control_surface_deflection
                                                    if inner_xsec.control_surface_type == "asymmetric" else
                                                    inner_xsec.control_surface_deflection)
                                    ) * (1 - nondim_spanwise_coordinate) +
                                    outer_xsec.airfoil.CDp_function(
                                        alpha=alpha, Re=Re, mach=mach,
                                        deflection=(-inner_xsec.control_surface_deflection
                                                    if inner_xsec.control_surface_type == "asymmetric" else
                                                    inner_xsec.control_surface_deflection)
                                    ) * nondim_spanwise_coordinate
                            )
                        )
                        Cm_functions.append(
                            lambda alpha, Re, mach,
                                   inner_xsec=inner_xsec,
                                   outer_xsec=outer_xsec,
                                   nondim_spanwise_coordinate=nondim_spanwise_coordinate,
                            : (
                                    inner_xsec.airfoil.Cm_function(
                                        alpha=alpha, Re=Re, mach=mach,
                                        deflection=(-inner_xsec.control_surface_deflection
                                                    if inner_xsec.control_surface_type == "asymmetric" else
                                                    inner_xsec.control_surface_deflection)
                                    ) * (1 - nondim_spanwise_coordinate) +
                                    outer_xsec.airfoil.Cm_function(
                                        alpha=alpha, Re=Re, mach=mach,
                                        deflection=(-inner_xsec.control_surface_deflection
                                                    if inner_xsec.control_surface_type == "asymmetric" else
                                                    inner_xsec.control_surface_deflection)
                                    ) * nondim_spanwise_coordinate
                            )
                        )
                        wing_id.append(wing_num)

        # Concatenate things (DX)
        self.front_left_vertices = cas.transpose(cas.horzcat(*front_left_vertices))
        self.front_right_vertices = cas.transpose(cas.horzcat(*front_right_vertices))
        self.back_left_vertices = cas.transpose(cas.horzcat(*back_left_vertices))
        self.back_right_vertices = cas.transpose(cas.horzcat(*back_right_vertices))
        self.CL_functions = CL_functions  # type: list # of callables
        self.CDp_functions = CDp_functions  # type: list # of callables
        self.Cm_functions = Cm_functions  # type: list # of callables
        self.wing_id = wing_id

        if self.symmetric_problem:
            self.use_symmetry = [self.airplane.wings[i].symmetric for i in self.wing_id]

        # # Concatenate things (MX)
        # self.front_left_vertices = cas.MX(cas.transpose(cas.horzcat(*front_left_vertices)))  # type: cas.MX
        # self.front_right_vertices = cas.MX(cas.transpose(cas.horzcat(*front_right_vertices)))  # type: cas.MX
        # self.back_left_vertices = cas.MX(cas.transpose(cas.horzcat(*back_left_vertices)))  # type: cas.MX
        # self.back_right_vertices = cas.MX(cas.transpose(cas.horzcat(*back_right_vertices)))  # type: cas.MX
        # self.CL_functions = CL_functions  # type: list # of callables
        # self.CDp_functions = CDp_functions  # type: list # of callables
        # self.Cm_functions = Cm_functions  # type: list # of callables

        # Do the vortex math
        self.left_vortex_vertices = 0.75 * self.front_left_vertices + 0.25 * self.back_left_vertices  # type: cas.MX
        self.right_vortex_vertices = 0.75 * self.front_right_vertices + 0.25 * self.back_right_vertices  # type: cas.MX
        self.vortex_centers = (self.left_vortex_vertices + self.right_vortex_vertices) / 2  # type: cas.MX
        self.vortex_bound_leg = (self.right_vortex_vertices - self.left_vortex_vertices)  # type: cas.MX

        # Calculate areas
        diag1 = self.front_right_vertices - self.back_left_vertices
        diag2 = self.front_left_vertices - self.back_right_vertices
        cross = cas.cross(diag1, diag2)
        cross_norm = cas.sqrt(cross[:, 0] ** 2 + cross[:, 1] ** 2 + cross[:, 2] ** 2)
        self.areas = cross_norm / 2

        # Calculate local frame and chord at each station
        self.normal_directions = cross / cross_norm
        chord_vectors = (
                (self.back_left_vertices + self.back_right_vertices) / 2 -
                (self.front_left_vertices + self.front_right_vertices) / 2
        )
        self.chords = cas.sqrt(chord_vectors[:, 0] ** 2 + chord_vectors[:, 1] ** 2 + chord_vectors[:, 2] ** 2)
        self.chordwise_directions = chord_vectors / self.chords
        self.wing_directions = self.vortex_bound_leg / cas.sqrt(
            self.vortex_bound_leg[:, 0] ** 2 +
            self.vortex_bound_leg[:, 1] ** 2 +
            self.vortex_bound_leg[:, 2] ** 2
        )
        self.local_forward_directions = cas.cross(self.normal_directions, self.wing_directions)

        # Do final processing for later use
        self.n_panels = self.front_left_vertices.shape[0]

        if self.verbose: print("Meshing complete!")

    def setup_geometry(self):
        if self.verbose: print("Calculating the vortex center velocity influence matrix...")
        self.Vij_x, self.Vij_y, self.Vij_z = self.calculate_Vij(self.vortex_centers)

    def setup_operating_point(self):
        if self.verbose: print("Calculating the freestream influence...")
        self.steady_freestream_velocity = self.op_point.compute_freestream_velocity_geometry_axes()  # Direction the wind is GOING TO, in geometry axes coordinates
        self.rotation_freestream_velocities = self.op_point.compute_rotation_velocity_geometry_axes(
            self.vortex_centers)
        self.freestream_velocities = cas.transpose(self.steady_freestream_velocity + cas.transpose(
            self.rotation_freestream_velocities))  # Nx3, represents the freestream velocity at each vortex center

    def calculate_vortex_strengths(self):
        if self.verbose: print("Calculating vortex strengths...")

        # Set up implicit solve (explicit is not possible for general nonlinear problem)
        self.vortex_strengths = self.opti.variable(self.n_panels)
        self.opti.set_initial(self.vortex_strengths, 0)

        # Find velocities
        self.induced_velocities = cas.horzcat(
            self.Vij_x @ self.vortex_strengths,
            self.Vij_y @ self.vortex_strengths,
            self.Vij_z @ self.vortex_strengths,
        )
        self.velocities = self.induced_velocities + self.freestream_velocities
        self.alpha_eff_perpendiculars = cas.atan2(
            (
                    self.velocities[:, 0] * self.normal_directions[:, 0] +
                    self.velocities[:, 1] * self.normal_directions[:, 1] +
                    self.velocities[:, 2] * self.normal_directions[:, 2]
            ),
            (
                    self.velocities[:, 0] * -self.local_forward_directions[:, 0] +
                    self.velocities[:, 1] * -self.local_forward_directions[:, 1] +
                    self.velocities[:, 2] * -self.local_forward_directions[:, 2]
            )
        ) * (180 / cas.pi)
        self.velocity_magnitudes = cas.sqrt(
            self.velocities[:, 0] ** 2 +
            self.velocities[:, 1] ** 2 +
            self.velocities[:, 2] ** 2
        )
        self.Res = self.op_point.density * self.velocity_magnitudes * self.chords / self.op_point.viscosity
        self.machs = [self.op_point.mach] * self.n_panels  # TODO incorporate sweep effects here!

        # Get perpendicular parameters
        self.cos_sweeps = (
                                  self.velocities[:, 0] * -self.local_forward_directions[:, 0] +
                                  self.velocities[:, 1] * -self.local_forward_directions[:, 1] +
                                  self.velocities[:, 2] * -self.local_forward_directions[:, 2]
                          ) / self.velocity_magnitudes
        self.chord_perpendiculars = self.chords * self.cos_sweeps
        self.velocity_magnitude_perpendiculars = self.velocity_magnitudes * self.cos_sweeps
        self.Res_perpendicular = self.Res * self.cos_sweeps
        self.machs_perpendicular = self.machs * self.cos_sweeps

        CL_locals = [
            self.CL_functions[i](
                alpha=self.alpha_eff_perpendiculars[i],
                Re=self.Res_perpendicular[i],
                mach=self.machs_perpendicular[i],
            ) for i in range(self.n_panels)
        ]
        CDp_locals = [
            self.CDp_functions[i](
                alpha=self.alpha_eff_perpendiculars[i],
                Re=self.Res_perpendicular[i],
                mach=self.machs_perpendicular[i],
            ) for i in range(self.n_panels)
        ]
        Cm_locals = [
            self.Cm_functions[i](
                alpha=self.alpha_eff_perpendiculars[i],
                Re=self.Res_perpendicular[i],
                mach=self.machs_perpendicular[i],
            ) for i in range(self.n_panels)
        ]
        self.CL_locals = cas.vertcat(*CL_locals)
        self.CDp_locals = cas.vertcat(*CDp_locals)
        self.Cm_locals = cas.vertcat(*Cm_locals)

        self.Vi_cross_li = cas.horzcat(
            self.velocities[:, 1] * self.vortex_bound_leg[:, 2] - self.velocities[:, 2] * self.vortex_bound_leg[:, 1],
            self.velocities[:, 2] * self.vortex_bound_leg[:, 0] - self.velocities[:, 0] * self.vortex_bound_leg[:, 2],
            self.velocities[:, 0] * self.vortex_bound_leg[:, 1] - self.velocities[:, 1] * self.vortex_bound_leg[:, 0],
        )
        Vi_cross_li_magnitudes = cas.sqrt(
            self.Vi_cross_li[:, 0] ** 2 +
            self.Vi_cross_li[:, 1] ** 2 +
            self.Vi_cross_li[:, 2] ** 2
        )

        # self.opti.subject_to([
        #     self.vortex_strengths * Vi_cross_li_magnitudes ==
        #     0.5 * self.velocity_magnitude_perpendiculars ** 2 * self.CL_locals * self.areas
        # ])
        self.opti.subject_to([
            self.vortex_strengths * Vi_cross_li_magnitudes * 2 / self.velocity_magnitude_perpendiculars ** 2 / self.areas
            ==
            self.CL_locals
        ])

    def calculate_forces(self):

        if self.verbose: print("Calculating induced forces...")
        forces_inviscid_geometry = self.op_point.density * self.Vi_cross_li * self.vortex_strengths
        force_total_inviscid_geometry = cas.vertcat(
            cas.sum1(forces_inviscid_geometry[:, 0]),
            cas.sum1(forces_inviscid_geometry[:, 1]),
            cas.sum1(forces_inviscid_geometry[:, 2]),
        )  # Remember, this is in GEOMETRY AXES, not WIND AXES or BODY AXES.
        if self.symmetric_problem:
            forces_inviscid_geometry_from_symmetry = cas.if_else(
                self.use_symmetry,
                reflect_over_XZ_plane(forces_inviscid_geometry),
                0
            )
            force_total_inviscid_geometry_from_symmetry = cas.vertcat(
                cas.sum1(forces_inviscid_geometry_from_symmetry[:, 0]),
                cas.sum1(forces_inviscid_geometry_from_symmetry[:, 1]),
                cas.sum1(forces_inviscid_geometry_from_symmetry[:, 2]),
            )
            force_total_inviscid_geometry += force_total_inviscid_geometry_from_symmetry
        self.force_total_inviscid_wind = cas.transpose(
            self.op_point.compute_rotation_matrix_wind_to_geometry()) @ force_total_inviscid_geometry

        if self.verbose: print("Calculating induced moments...")
        moments_inviscid_geometry = cas.cross(
            cas.transpose(cas.transpose(self.vortex_centers) - self.airplane.xyz_ref),
            forces_inviscid_geometry
        )
        moment_total_inviscid_geometry = cas.vertcat(
            cas.sum1(moments_inviscid_geometry[:, 0]),
            cas.sum1(moments_inviscid_geometry[:, 1]),
            cas.sum1(moments_inviscid_geometry[:, 2]),
        )  # Remember, this is in GEOMETRY AXES, not WIND AXES or BODY AXES.
        if self.symmetric_problem:
            moments_inviscid_geometry_from_symmetry = cas.if_else(
                self.use_symmetry,
                -reflect_over_XZ_plane(moments_inviscid_geometry),
                0
            )
            moment_total_inviscid_geometry_from_symmetry = cas.vertcat(
                cas.sum1(moments_inviscid_geometry_from_symmetry[:, 0]),
                cas.sum1(moments_inviscid_geometry_from_symmetry[:, 1]),
                cas.sum1(moments_inviscid_geometry_from_symmetry[:, 2]),
            )
            moment_total_inviscid_geometry += moment_total_inviscid_geometry_from_symmetry
        self.moment_total_inviscid_wind = cas.transpose(
            self.op_point.compute_rotation_matrix_wind_to_geometry()) @ moment_total_inviscid_geometry

        if self.verbose: print("Calculating profile forces...")
        forces_profile_geometry = (
                (0.5 * self.op_point.density * self.velocity_magnitudes * self.velocities)
                * self.CDp_locals * self.areas
        )
        force_total_profile_geometry = cas.vertcat(
            cas.sum1(forces_profile_geometry[:, 0]),
            cas.sum1(forces_profile_geometry[:, 1]),
            cas.sum1(forces_profile_geometry[:, 2]),
        )
        if self.symmetric_problem:
            forces_profile_geometry_from_symmetry = cas.if_else(
                self.use_symmetry,
                reflect_over_XZ_plane(forces_profile_geometry),
                0
            )
            force_total_profile_geometry_from_symmetry = cas.vertcat(
                cas.sum1(forces_profile_geometry_from_symmetry[:, 0]),
                cas.sum1(forces_profile_geometry_from_symmetry[:, 1]),
                cas.sum1(forces_profile_geometry_from_symmetry[:, 2]),
            )
            force_total_profile_geometry += force_total_profile_geometry_from_symmetry
        self.force_total_profile_wind = cas.transpose(
            self.op_point.compute_rotation_matrix_wind_to_geometry()) @ force_total_profile_geometry

        if self.verbose: print("Calculating profile moments...")
        moments_profile_geometry = cas.cross(
            cas.transpose(cas.transpose(self.vortex_centers) - self.airplane.xyz_ref),
            forces_profile_geometry
        )
        moment_total_profile_geometry = cas.vertcat(
            cas.sum1(moments_profile_geometry[:, 0]),
            cas.sum1(moments_profile_geometry[:, 1]),
            cas.sum1(moments_profile_geometry[:, 2]),
        )
        if self.symmetric_problem:
            moments_profile_geometry_from_symmetry = cas.if_else(
                self.use_symmetry,
                -reflect_over_XZ_plane(moments_profile_geometry),
                0
            )
            moment_total_profile_geometry_from_symmetry = cas.vertcat(
                cas.sum1(moments_profile_geometry_from_symmetry[:, 0]),
                cas.sum1(moments_profile_geometry_from_symmetry[:, 1]),
                cas.sum1(moments_profile_geometry_from_symmetry[:, 2]),
            )
            moment_total_profile_geometry += moment_total_profile_geometry_from_symmetry
        self.moment_total_profile_wind = cas.transpose(
            self.op_point.compute_rotation_matrix_wind_to_geometry()) @ moment_total_profile_geometry

        if self.verbose: print("Calculating pitching moments...")
        bound_leg_YZ = self.vortex_bound_leg
        bound_leg_YZ[:, 0] = 0
        moments_pitching_geometry = (
                (0.5 * self.op_point.density * self.velocity_magnitudes ** 2) *
                self.Cm_locals * self.chords ** 2 * bound_leg_YZ
        )
        moment_total_pitching_geometry = cas.vertcat(
            cas.sum1(moments_pitching_geometry[:, 0]),
            cas.sum1(moments_pitching_geometry[:, 1]),
            cas.sum1(moments_pitching_geometry[:, 2]),
        )
        if self.symmetric_problem:
            moments_pitching_geometry_from_symmetry = cas.if_else(
                self.use_symmetry,
                -reflect_over_XZ_plane(moments_pitching_geometry),
                0
            )
            moment_total_pitching_geometry_from_symmetry = cas.vertcat(
                cas.sum1(moments_pitching_geometry_from_symmetry[:, 0]),
                cas.sum1(moments_pitching_geometry_from_symmetry[:, 1]),
                cas.sum1(moments_pitching_geometry_from_symmetry[:, 2]),
            )
            moment_total_pitching_geometry += moment_total_pitching_geometry_from_symmetry
        self.moment_total_pitching_wind = cas.transpose(
            self.op_point.compute_rotation_matrix_wind_to_geometry()) @ moment_total_pitching_geometry

        if self.verbose: print("Calculating total forces and moments...")
        self.force_total_wind = self.force_total_inviscid_wind + self.force_total_profile_wind
        self.moment_total_wind = self.moment_total_inviscid_wind + self.moment_total_profile_wind

        # Calculate nondimensional forces
        q = self.op_point.dynamic_pressure()
        s_ref = self.airplane.s_ref
        b_ref = self.airplane.b_ref
        c_ref = self.airplane.c_ref
        self.CL = -self.force_total_wind[2] / q / s_ref
        self.CD = -self.force_total_wind[0] / q / s_ref
        self.CDi = -self.force_total_inviscid_wind[0] / q / s_ref
        self.CDp = -self.force_total_profile_wind[0] / q / s_ref
        self.CY = self.force_total_wind[1] / q / s_ref
        self.Cl = self.moment_total_wind[0] / q / s_ref / b_ref
        self.Cm = self.moment_total_wind[1] / q / s_ref / c_ref
        self.Cn = self.moment_total_wind[2] / q / s_ref / b_ref

        # Solves divide by zero error
        self.CL_over_CD = cas.if_else(self.CD == 0, 0, self.CL / self.CD)

    def calculate_Vij(self,
                      points,  # type: cas.MX
                      align_trailing_vortices_with_freestream=True,  # Otherwise, aligns with x-axis
                      ):
        # Calculates Vij, the velocity influence matrix (First index is collocation point number, second index is vortex number).
        # points: the list of points (Nx3) to calculate the velocity influence at.

        n_points = points.shape[0]

        # Make a and b vectors.
        # a: Vector from all collocation points to all horseshoe vortex left vertices.
        #   # First index is collocation point #, second is vortex #.
        # b: Vector from all collocation points to all horseshoe vortex right vertices.
        #   # First index is collocation point #, second is vortex #.
        a_x = points[:, 0] - cas.repmat(cas.transpose(self.left_vortex_vertices[:, 0]), n_points, 1)
        a_y = points[:, 1] - cas.repmat(cas.transpose(self.left_vortex_vertices[:, 1]), n_points, 1)
        a_z = points[:, 2] - cas.repmat(cas.transpose(self.left_vortex_vertices[:, 2]), n_points, 1)
        b_x = points[:, 0] - cas.repmat(cas.transpose(self.right_vortex_vertices[:, 0]), n_points, 1)
        b_y = points[:, 1] - cas.repmat(cas.transpose(self.right_vortex_vertices[:, 1]), n_points, 1)
        b_z = points[:, 2] - cas.repmat(cas.transpose(self.right_vortex_vertices[:, 2]), n_points, 1)

        if align_trailing_vortices_with_freestream:
            freestream_direction = self.op_point.compute_freestream_direction_geometry_axes()
            u_x = freestream_direction[0]
            u_y = freestream_direction[1]
            u_z = freestream_direction[2]
        else:
            u_x = 1
            u_y = 0
            u_z = 0

        # Do some useful arithmetic
        a_cross_b_x = a_y * b_z - a_z * b_y
        a_cross_b_y = a_z * b_x - a_x * b_z
        a_cross_b_z = a_x * b_y - a_y * b_x
        a_dot_b = a_x * b_x + a_y * b_y + a_z * b_z

        a_cross_u_x = a_y * u_z - a_z * u_y
        a_cross_u_y = a_z * u_x - a_x * u_z
        a_cross_u_z = a_x * u_y - a_y * u_x
        a_dot_u = a_x * u_x + a_y * u_y + a_z * u_z

        b_cross_u_x = b_y * u_z - b_z * u_y
        b_cross_u_y = b_z * u_x - b_x * u_z
        b_cross_u_z = b_x * u_y - b_y * u_x
        b_dot_u = b_x * u_x + b_y * u_y + b_z * u_z

        norm_a = cas.sqrt(a_x ** 2 + a_y ** 2 + a_z ** 2)
        norm_b = cas.sqrt(b_x ** 2 + b_y ** 2 + b_z ** 2)
        norm_a_inv = 1 / norm_a
        norm_b_inv = 1 / norm_b

        # Handle the special case where the collocation point is along the bound vortex leg
        a_cross_b_squared = (
                a_cross_b_x ** 2 +
                a_cross_b_y ** 2 +
                a_cross_b_z ** 2
        )
        a_dot_b = cas.if_else(a_cross_b_squared < 1e-8, a_dot_b + 1, a_dot_b)

        # Calculate Vij
        term1 = (norm_a_inv + norm_b_inv) / (norm_a * norm_b + a_dot_b)
        term2 = norm_a_inv / (norm_a - a_dot_u)
        term3 = norm_b_inv / (norm_b - b_dot_u)

        Vij_x = 1 / (4 * np.pi) * (
                a_cross_b_x * term1 +
                a_cross_u_x * term2 -
                b_cross_u_x * term3
        )
        Vij_y = 1 / (4 * np.pi) * (
                a_cross_b_y * term1 +
                a_cross_u_y * term2 -
                b_cross_u_y * term3
        )
        Vij_z = 1 / (4 * np.pi) * (
                a_cross_b_z * term1 +
                a_cross_u_z * term2 -
                b_cross_u_z * term3
        )
        if self.symmetric_problem:  # If it's a symmetric problem, you've got to add the other side's influence.

            # If it is symmetric, re-do it with flipped coordinates

            # Make a and b vectors.
            # a: Vector from all collocation points to all horseshoe vortex left vertices.
            #   # First index is collocation point #, second is vortex #.
            # b: Vector from all collocation points to all horseshoe vortex right vertices.
            #   # First index is collocation point #, second is vortex #.
            a_x = points[:, 0] - cas.repmat(cas.transpose(self.right_vortex_vertices[:, 0]), n_points, 1)
            a_y = points[:, 1] - cas.repmat(cas.transpose(-self.right_vortex_vertices[:, 1]), n_points, 1)
            a_z = points[:, 2] - cas.repmat(cas.transpose(self.right_vortex_vertices[:, 2]), n_points, 1)
            b_x = points[:, 0] - cas.repmat(cas.transpose(self.left_vortex_vertices[:, 0]), n_points, 1)
            b_y = points[:, 1] - cas.repmat(cas.transpose(-self.left_vortex_vertices[:, 1]), n_points, 1)
            b_z = points[:, 2] - cas.repmat(cas.transpose(self.left_vortex_vertices[:, 2]), n_points, 1)

            # Do some useful arithmetic
            a_cross_b_x = a_y * b_z - a_z * b_y
            a_cross_b_y = a_z * b_x - a_x * b_z
            a_cross_b_z = a_x * b_y - a_y * b_x
            a_dot_b = a_x * b_x + a_y * b_y + a_z * b_z

            a_cross_u_x = a_y * u_z - a_z * u_y
            a_cross_u_y = a_z * u_x - a_x * u_z
            a_cross_u_z = a_x * u_y - a_y * u_x
            a_dot_u = a_x * u_x + a_y * u_y + a_z * u_z

            b_cross_u_x = b_y * u_z - b_z * u_y
            b_cross_u_y = b_z * u_x - b_x * u_z
            b_cross_u_z = b_x * u_y - b_y * u_x
            b_dot_u = b_x * u_x + b_y * u_y + b_z * u_z

            norm_a = cas.sqrt(a_x ** 2 + a_y ** 2 + a_z ** 2)
            norm_b = cas.sqrt(b_x ** 2 + b_y ** 2 + b_z ** 2)
            norm_a_inv = 1 / norm_a
            norm_b_inv = 1 / norm_b

            # Handle the special case where the collocation point is along the bound vortex leg
            a_cross_b_squared = (
                    a_cross_b_x ** 2 +
                    a_cross_b_y ** 2 +
                    a_cross_b_z ** 2
            )
            a_dot_b = cas.if_else(a_cross_b_squared < 1e-8, a_dot_b + 1, a_dot_b)

            # Calculate Vij
            term1 = (norm_a_inv + norm_b_inv) / (norm_a * norm_b + a_dot_b)
            term2 = norm_a_inv / (norm_a - a_dot_u)
            term3 = norm_b_inv / (norm_b - b_dot_u)

            Vij_x_from_symmetry = 1 / (4 * np.pi) * (
                    a_cross_b_x * term1 +
                    a_cross_u_x * term2 -
                    b_cross_u_x * term3
            )
            Vij_y_from_symmetry = 1 / (4 * np.pi) * (
                    a_cross_b_y * term1 +
                    a_cross_u_y * term2 -
                    b_cross_u_y * term3
            )
            Vij_z_from_symmetry = 1 / (4 * np.pi) * (
                    a_cross_b_z * term1 +
                    a_cross_u_z * term2 -
                    b_cross_u_z * term3
            )

            Vij_x += cas.transpose(cas.if_else(self.use_symmetry, cas.transpose(Vij_x_from_symmetry), 0))
            Vij_y += cas.transpose(cas.if_else(self.use_symmetry, cas.transpose(Vij_y_from_symmetry), 0))
            Vij_z += cas.transpose(cas.if_else(self.use_symmetry, cas.transpose(Vij_z_from_symmetry), 0))

        return Vij_x, Vij_y, Vij_z

    def get_induced_velocity_at_point(self, point):
        if not self.opti.return_status() == 'Solve_Succeeded':
            print("WARNING: This method should only be used after a solution has been found!!!\n"
                  "Running anyway for debugging purposes - this is likely to not work.")

        Vij_x, Vij_y, Vij_z = self.calculate_Vij(point)

        vortex_strengths = self.opti.debug.value(self.vortex_strengths)

        Vi_x = Vij_x @ vortex_strengths
        Vi_y = Vij_y @ vortex_strengths
        Vi_z = Vij_z @ vortex_strengths

        Vi = np.hstack((Vi_x, Vi_y, Vi_z))

        return Vi

    def get_velocity_at_point(self, point):
        # Input: attrib_name Nx3 numpy array of points that you would like to know the velocities at.
        # Output: attrib_name Nx3 numpy array of the velocities at those points.

        Vi = self.get_induced_velocity_at_point(point)

        freestream = self.op_point.compute_freestream_velocity_geometry_axes()

        V = cas.transpose(cas.transpose(Vi) + freestream)
        return V

    def calculate_streamlines(self,
                              seed_points=None,  # will be auto-calculated if not specified
                              n_steps=100,  # minimum of 2
                              length=None  # will be auto-calculated if not specified
                              ):

        if length is None:
            length = self.airplane.c_ref * 5
        if seed_points is None:
            seed_points = (self.back_left_vertices + self.back_right_vertices) / 2

        # Resolution
        length_per_step = length / n_steps

        # Initialize
        streamlines = [seed_points]

        # Iterate
        for step_num in range(1, n_steps):
            update_amount = self.get_velocity_at_point(streamlines[-1])
            norm_update_amount = cas.sqrt(
                update_amount[:, 0] ** 2 + update_amount[:, 1] ** 2 + update_amount[:, 2] ** 2)
            update_amount = length_per_step * update_amount / norm_update_amount
            streamlines.append(streamlines[-1] + update_amount)

        self.streamlines = streamlines

    def draw(self, data_to_plot=None, data_name=None, show=True, draw_streamlines=True, recalculate_streamlines=False):
        """
        Draws the solution. Note: Must be called on a SOLVED AeroProblem object.
        To solve an AeroProblem, use opti.solve(). To substitute a solved solution, use ap = ap.substitute_solution(sol).
        :return:
        """
        print("Drawing...")

        if not self.opti.return_status() == 'Solve_Succeeded':
            print("WARNING: This method should only be used after a solution has been found!\n"
                  "Running anyway for debugging purposes - this is likely to not work...")

        # Do substitutions
        get = lambda x: self.opti.debug.value(x)
        front_left_vertices = get(self.front_left_vertices)
        front_right_vertices = get(self.front_right_vertices)
        back_left_vertices = get(self.back_left_vertices)
        back_right_vertices = get(self.back_right_vertices)
        left_vortex_vertices = get(self.left_vortex_vertices)
        right_vortex_vertices = get(self.right_vortex_vertices)
        try:
            data_to_plot = get(data_to_plot)
        except NotImplementedError:
            pass

        if data_to_plot is None:
            CL_locals = get(self.CL_locals)
            chords = get(self.chords)
            c_ref = get(self.airplane.c_ref)
            data_name = "Cl * c / c_ref"
            data_to_plot = CL_locals * chords / c_ref

        fig = go.Figure()

        # x, y, and z give the vertices
        x = []
        y = []
        z = []
        # i, j and k give the connectivity of the vertices
        i = []
        j = []
        k = []
        intensity = []
        # xe, ye, and ze give the outline of each panel
        xe = []
        ye = []
        ze = []

        for index in range(len(front_left_vertices)):
            x.append(front_left_vertices[index, 0])
            x.append(front_right_vertices[index, 0])
            x.append(back_right_vertices[index, 0])
            x.append(back_left_vertices[index, 0])
            y.append(front_left_vertices[index, 1])
            y.append(front_right_vertices[index, 1])
            y.append(back_right_vertices[index, 1])
            y.append(back_left_vertices[index, 1])
            z.append(front_left_vertices[index, 2])
            z.append(front_right_vertices[index, 2])
            z.append(back_right_vertices[index, 2])
            z.append(back_left_vertices[index, 2])
            intensity.append(data_to_plot[index])
            intensity.append(data_to_plot[index])
            intensity.append(data_to_plot[index])
            intensity.append(data_to_plot[index])
            xe.append(front_left_vertices[index, 0])
            xe.append(front_right_vertices[index, 0])
            xe.append(back_right_vertices[index, 0])
            xe.append(back_left_vertices[index, 0])
            ye.append(front_left_vertices[index, 1])
            ye.append(front_right_vertices[index, 1])
            ye.append(back_right_vertices[index, 1])
            ye.append(back_left_vertices[index, 1])
            ze.append(front_left_vertices[index, 2])
            ze.append(front_right_vertices[index, 2])
            ze.append(back_right_vertices[index, 2])
            ze.append(back_left_vertices[index, 2])
            xe.append(None)
            ye.append(None)
            ze.append(None)
            xe.append(left_vortex_vertices[index, 0])
            xe.append(right_vortex_vertices[index, 0])
            ye.append(left_vortex_vertices[index, 1])
            ye.append(right_vortex_vertices[index, 1])
            ze.append(left_vortex_vertices[index, 2])
            ze.append(right_vortex_vertices[index, 2])
            xe.append(None)
            ye.append(None)
            ze.append(None)

            indices_added = np.arange(len(x) - 4, len(x))

            # Add front_left triangle
            i.append(indices_added[0])
            j.append(indices_added[1])
            k.append(indices_added[3])
            # Add back-right triangle
            i.append(indices_added[2])
            j.append(indices_added[3])
            k.append(indices_added[1])

            if self.symmetric_problem:
                if self.use_symmetry[index]:
                    x.append(front_left_vertices[index, 0])
                    x.append(front_right_vertices[index, 0])
                    x.append(back_right_vertices[index, 0])
                    x.append(back_left_vertices[index, 0])
                    y.append(-front_left_vertices[index, 1])
                    y.append(-front_right_vertices[index, 1])
                    y.append(-back_right_vertices[index, 1])
                    y.append(-back_left_vertices[index, 1])
                    z.append(front_left_vertices[index, 2])
                    z.append(front_right_vertices[index, 2])
                    z.append(back_right_vertices[index, 2])
                    z.append(back_left_vertices[index, 2])
                    intensity.append(data_to_plot[index])
                    intensity.append(data_to_plot[index])
                    intensity.append(data_to_plot[index])
                    intensity.append(data_to_plot[index])
                    xe.append(front_left_vertices[index, 0])
                    xe.append(front_right_vertices[index, 0])
                    xe.append(back_right_vertices[index, 0])
                    xe.append(back_left_vertices[index, 0])
                    ye.append(-front_left_vertices[index, 1])
                    ye.append(-front_right_vertices[index, 1])
                    ye.append(-back_right_vertices[index, 1])
                    ye.append(-back_left_vertices[index, 1])
                    ze.append(front_left_vertices[index, 2])
                    ze.append(front_right_vertices[index, 2])
                    ze.append(back_right_vertices[index, 2])
                    ze.append(back_left_vertices[index, 2])
                    xe.append(None)
                    ye.append(None)
                    ze.append(None)
                    xe.append(left_vortex_vertices[index, 0])
                    xe.append(right_vortex_vertices[index, 0])
                    ye.append(-left_vortex_vertices[index, 1])
                    ye.append(-right_vortex_vertices[index, 1])
                    ze.append(left_vortex_vertices[index, 2])
                    ze.append(right_vortex_vertices[index, 2])
                    xe.append(None)
                    ye.append(None)
                    ze.append(None)

                    indices_added = np.arange(len(x) - 4, len(x))

                    # Add front_left triangle
                    i.append(indices_added[0])
                    j.append(indices_added[1])
                    k.append(indices_added[3])
                    # Add back-right triangle
                    i.append(indices_added[2])
                    j.append(indices_added[3])
                    k.append(indices_added[1])

        fig.add_trace(
            go.Mesh3d(
                x=x,
                y=y,
                z=z,
                i=i,
                j=j,
                k=k,
                flatshading=False,
                intensity=intensity,
                colorscale="Viridis",
                colorbar=dict(
                    title=data_name,
                    titleside="top",
                    ticks="outside"
                )
            )
        )

        # define the trace for triangle sides
        fig.add_trace(
            go.Scatter3d(
                x=xe,
                y=ye,
                z=ze,
                mode='lines',
                name='',
                line=dict(color='rgb(0,0,0)', width=2),
                showlegend=False
            )
        )

        if draw_streamlines:
            if (not hasattr(self, 'streamlines')) or recalculate_streamlines:
                if self.verbose: print("Calculating streamlines...")
                seed_points = (self.back_left_vertices + self.back_right_vertices) / 2
                self.calculate_streamlines(seed_points=seed_points)

            if self.verbose: print("Parsing streamline data...")
            n_streamlines = self.streamlines[0].shape[0]
            n_timesteps = len(self.streamlines)

            xs = []
            ys = []
            zs = []

            for streamlines_num in range(n_streamlines):
                xs.extend([float(self.streamlines[ts][streamlines_num, 0]) for ts in range(n_timesteps)])
                ys.extend([float(self.streamlines[ts][streamlines_num, 1]) for ts in range(n_timesteps)])
                zs.extend([float(self.streamlines[ts][streamlines_num, 2]) for ts in range(n_timesteps)])

                xs.append(None)
                ys.append(None)
                zs.append(None)

                if self.symmetric_problem:  # TODO consider removing redundant plotting of centerline surfaces (low priority)
                    xs.extend([float(self.streamlines[ts][streamlines_num, 0]) for ts in range(n_timesteps)])
                    ys.extend([-float(self.streamlines[ts][streamlines_num, 1]) for ts in range(n_timesteps)])
                    zs.extend([float(self.streamlines[ts][streamlines_num, 2]) for ts in range(n_timesteps)])

                    xs.append(None)
                    ys.append(None)
                    zs.append(None)

            fig.add_trace(
                go.Scatter3d(
                    x=xs,
                    y=ys,
                    z=zs,
                    mode='lines',
                    name='',
                    line=dict(color='rgba(119,0,255,200)', width=1),
                    showlegend=False
                )
            )

        fig.update_layout(
            title="%s Airplane, CasLL1 Solution" % self.airplane.name,
            scene=dict(aspectmode='data'),

        )

        if show: fig.show()

        return fig