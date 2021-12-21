import numpy as np
import casadi
import forcespro
import forcespro.nlp
import matplotlib.pyplot as plt
from reference_path import ReferencePath
from commonroad.common.file_reader import CommonRoadFileReader
from solvers import SolverForcespro
import sys
sys.path.append("..")


def main():
    # define the scenario and planning problem
    # # generate reference path for car to follow-----> scenario 1
    # path_points = ReferencePath(path_scenario="/home/xin/PycharmProjects/MPFAV_MPC/scenarios/",
    #                             id_scenario="ZAM_Tutorial_Urban-3_2.xml").reference_path.T  # transpose
    # generate reference path for car to follow-----> scenario 2
    path_scenario = "/home/xin/PycharmProjects/MPFAV_MPC/scenarios/"
    id_scenario = "USA_Lanker-2_18_T-1.xml"
    scenario, planning_problem_set = CommonRoadFileReader(path_scenario + id_scenario).open()
    planning_problem = list(planning_problem_set.planning_problem_dict.values())[0]

    reference_path_instance = ReferencePath(scenario, planning_problem)

    path_points = reference_path_instance.reference_path.T  # transpose
    init_position, init_acceleration, init_orientation = reference_path_instance.get_init_value()
    desired_velocity, delta_t = reference_path_instance.get_desired_velocity_and_delta_t()

    SolverForcespro_instance = SolverForcespro()
    # generate code for estimator
    model, solver = SolverForcespro_instance.generate_pathplanner()

    # Simulation
    # ----------
    reference_path_distance = reference_path_instance.accumulated_distance_in_reference_path[-1]
    # print(reference_path_instance.accumulated_distance_in_reference_path)
    desired_time = reference_path_distance / desired_velocity
    sim_length = int(desired_time/0.1)
    # sim_length = 300  # simulate 30sec

    # Variables for storing simulation data
    x = np.zeros((5, sim_length + 1))  # states
    u = np.zeros((2, sim_length))  # inputs

    # Set initial guess to start solver from
    x0i = np.zeros((model.nvar, 1))  # model.nvar = 7 变量个数  shape of x0i = [7, 1]
    x0 = np.transpose(np.tile(x0i, (1, model.N)))  # # horizon length 10,  shape of x0 = [10, 7]

    xinit = np.transpose(np.array([path_points[0, 0], path_points[1, 0], 0., 0., init_orientation]))  # Set initial states
    # state x = [xPos,yPos,delta,v,psi]
    x[:, 0] = xinit

    problem = {"x0": x0,
               "xinit": xinit}

    start_pred = np.reshape(problem["x0"], (7, model.N))  # first prdicition corresponds to initial guess

    # generate plot with initial values
    SolverForcespro_instance.createPlot(x, u, start_pred, sim_length, model, path_points, xinit)

    # Simulation
    for k in range(sim_length):
        print("k=", k)

        # Objective function   因为object function 有变量是随着时间的变化而变化的，所以要写在 main里的for 循环中
        # model.objective = obj
        print("current desired distance", desired_velocity*k*0.1)
        print("desired_index", reference_path_instance.find_nearest_point_in_reference_path(k*0.1))
        currrent_target = path_points.T[reference_path_instance.find_nearest_point_in_reference_path(k*0.1)+6]
        model.objective = lambda z, currrent_target=currrent_target: (200.0 * (z[2] - currrent_target[0]) ** 2  # costs on deviating on the path in x-direction
                                        + 200.0 * (z[3] - currrent_target[1]) ** 2  # costs on deviating on the path in y-direction
                                        + 0.1 * z[4] ** 2  # penalty on steering angle
                                        + 200 * (z[5] - desired_velocity) ** 2  # penalty on velocity
                                        + 0.1 * z[6] ** 2
                                        + 0.1 * z[0] ** 2  # penalty on input velocity of steering angle
                                        + 0.1 * z[1] ** 2)  # penalty on input longitudinal acceleration
        # model.objectiveN = objN  # increased costs for the last stage
        model.objectiveN = lambda z, currrent_target=currrent_target: (400.0 * (z[2] - currrent_target[0]) ** 2  # costs on deviating on the path in x-direction
                                      + 400.0 * (z[3] - currrent_target[1]) ** 2  # costs on deviating on the path in y-direction
                                      + 0.2 * z[4] ** 2  # penalty on steering angle
                                      + 200 * (z[5] - desired_velocity) ** 2  # penalty on velocity
                                      + 0.2 * z[6] ** 2
                                      + 0.2 * z[0] ** 2  # penalty on input velocity of steering angle
                                      + 0.2 * z[1] ** 2)  # penalty on input longitudinal acceleration
        # The function must be able to handle symbolic evaluation,
        # by passing in CasADi symbols. This means certain numpy funcions are not available.

        # Set initial condition
        problem["xinit"] = x[:, k]

        # Set runtime parameters (here, the next N points on the path)
        next_path_points = extract_next_path_points(path_points, x[0:2, k], model.N)
        # 返回离x[0:2, k]最近的点的之后的N个点 不包括本身，shape=2*N
        problem["all_parameters"] = np.reshape(np.transpose(next_path_points), (2 * model.N, 1))
        # shape = 2N * 1  【x y x y x y...】.T

        # Time to solve the NLP!
        output, exitflag, info = solver.solve(problem)
        print("exitflag = ", exitflag)

        # Make sure the solver has exited properly.
        assert exitflag == 1, "bad exitflag"  # 不成立， 返回AssertionError: bad exitflag
        sys.stderr.write("FORCESPRO took {} iterations and {} seconds to solve the problem.\n"
                         .format(info.it, info.solvetime))

        # Extract output
        temp = np.zeros((np.max(model.nvar), model.N))  # 初始化 temp.shape=7*N
        for i in range(0, model.N):
            temp[:, i] = output['x{0:02d}'.format(i + 1)]
        pred_u = temp[0:2, :]   # inputs的N个预测值 2*N
        pred_x = temp[2:7, :]   # states的N个预测值 5*N

        # Apply optimized input u of first stage to system and save simulation data
        u[:, k] = pred_u[:, 0]
        x[:, k + 1] = np.transpose(model.eq(np.concatenate((u[:, k], x[:, k]))))  # 通过第k个step的state和u，来更新k+1时刻的state

        # plot results of current simulation step
        SolverForcespro_instance.updatePlots(x, u, pred_x, pred_u, model, k)

        if k == sim_length - 1:
            fig = plt.gcf()
            ax_list = fig.axes
            ax_list[0].get_lines().pop(-1).remove()  # remove old prediction of trajectory
            ax_list[0].legend(['desired trajectory', 'init pos', 'car trajectory'], loc='lower right')
            plt.show()
        else:
            plt.draw()


if __name__ == "__main__":
    main()