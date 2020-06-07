import cv2
import imageio
import numpy as np
import argparse
import imageio as img
import gc
from numba import jit

import seam
import spatial_coherence

# ------------Things to Implement-------------------

# TODO: Determine spatial coherence of removing a pixel
# TODO: Determine temporal coherence cost of removing a pixel
# TODO: Determine saliency of removing a pixel
# TODO: Combine the Sc, Tc, and S into a weighted ratio M
# TODO: Implement image seam carving algorithm from
#  (http://www.faculty.idc.ac.il/arik/SCWeb/imret/index.html) to minimize m

# OF COURSE THERE'S OTHER STUFF I JUST CANT THINK OF IT RN

# -----------Captures Video Input------------------
def read_video(name):
    cap = cv2.VideoCapture(name)

    # Check if successfully
    if (cap.isOpened() == False):
        print("Error opening video file at \'" + name + "\'")

    video = []  
    while (cap.isOpened()):
        # Capture frame-by-frame
        ret, frame = cap.read()
        if ret:
            video.append(frame)
        else:
            break
    cap.release()
    # 4-D np array: frames, height, width, rgb
    return video


def write_video(video, name, width, height):
    out = cv2.VideoWriter(name, cv2.VideoWriter_fourcc('m','p','4','v'), 30.0, (width, height))

    for frame in video:
        # write the flipped frame
        out.write(np.array(frame))

    # Release everything if job is finished
    out.release()


def rotate_image(image, clockwise):
    k = 1 if clockwise else 3
    return np.rot90(image, k)


def visualize(im, boolmask=None, rotate=False):
    SEAM_COLOR = np.array([255, 200, 200])
    vis = im.astype(np.uint8)
    if boolmask is not None:
        vis[np.where(boolmask == False)] = SEAM_COLOR
    if rotate:
        vis = rotate_image(vis, False)
    #cv2.imshow("visualization", vis)
    #cv2.waitKey(1)
    return vis

def saliency_map(gray_scale):
    height, width = gray_scale.shape[:2]

    energy = np.zeros((height, width))
    m = np.zeros((height, width))

    U = np.roll(gray_scale, 1, axis=0)
    L = np.roll(gray_scale, 1, axis=1)
    R = np.roll(gray_scale, -1, axis=1)

    cU = np.abs(R-L)
    cL = np.abs(U-L) + cU
    cR = np.abs(U - R) + cU

    for i in range(1, height):
        mU = m[i - 1]
        mL = np.roll(mU, 1)
        mR = np.roll(mU, -1)

        mULR = np.array([mU, mL, mR])
        cULR = np.array([cU[i], cL[i], cR[i]])
        mULR += cULR

        argmins = np.argmin(mULR, axis=0)
        m[i] = np.choose(argmins, mULR)
        energy[i] = np.choose(argmins, cULR)

    return energy.astype(np.uint8)

@jit(nopython=True)
def carve_seams_piecewise(frame, width):
    #print("Carving seams")
    energies = frame.copy()
    seams = [[0 for j in range(energies.shape[1])] for i in range(energies.shape[0])]

    for y in range(1, frame.shape[0]):
        for x in range(frame.shape[1]):
            leftBound = x - width if x > width else 0
            rightBound = x + width + 1 if x + width + 1 < frame.shape[1] else frame.shape[1] - 1

            min_energy = np.amin(energies[y-1][leftBound:rightBound])
            min_indexes = np.where(energies[y-1][leftBound:rightBound] == min_energy)[0]
            seams[y][x] = leftBound + min_indexes[0]

            energies[y][x] += min_energy

    return (np.array(seams), energies)

def get_seam(seams, n):
    seam = []
    for i in reversed(range(seams.shape[0])):
        seam.append([i, n])
        n = seams[i, n]
    return seam

def get_n_seams(seams, energies, n):
    newSeams = []
    modifiedEnergies = energies[-1].copy()
    newEnergies = []
    if (energies.shape[0] < n):
        raise Exception("Cannot get " + str(n) + " seams. Only " + energies.shape[0] + " exist")
    maxEnergy = np.max(energies)
    for i in range(0, n):
        minIndex = np.where(modifiedEnergies == np.min(modifiedEnergies))[0][0]
        modifiedEnergies[minIndex] = maxEnergy + 1
        newSeams.append(get_seam(seams, minIndex))
        newEnergies.append(energies[-1][minIndex])
    return (newSeams, newEnergies)

def remove_seam(frame, seams, n):
    mask = [[[1 for x in range(3)] for j in range(frame.shape[1])] for i in range(frame.shape[0])]
    for i in reversed(range(seams.shape[0])):
        mask[i][n] = [0, 0, 0]
        n = seams[i][n]
    mask = np.array(mask, dtype=bool)
    new_frame = frame[mask].reshape((frame.shape[0], frame.shape[1]-1, 3))
    return new_frame

def add_seams(frame, seams):
    # new_frame = [[frame[i][j] for j in range(frame.shape[1])] for i in range(frame.shape[0])]
    # for seam in seams:
    #     for point in seam:
    #         new_frame[point[1]].insert(point[0], frame[point[1]][point[0]])
    #
    # new_frame = np.array(new_frame)
    # return new_frame
    frameCopy = frame.copy()
    new_frame = np.zeros((frame.shape[0], frame.shape[1], frame.shape[2]))
    for seam in seams:
        new_frame = np.zeros((new_frame.shape[0], new_frame.shape[1] + 1, new_frame.shape[2]))
        for point in seam:
            new_frame[point[1]] = np.concatenate([frameCopy[point[1]][0:point[0]], np.array([frameCopy[point[1]][point[0]]]), frameCopy[point[1]][point[0]:]])
        frameCopy = new_frame
    return new_frame

def highlight_seam(frame, seam):
    new_frame = frame.copy()
    for pixel in seam:
        new_frame[pixel[0], pixel[1]] = [255, 180, 180]
    return new_frame

@jit(nopython=True)
def compute_temporal_coherence_cost(currentFrame, previousSeam):
    costMap = []
    for i in range(0, currentFrame.shape[0]):
        costMap.append([0 for x in range(currentFrame.shape[1])])
        cumulativeCost = 0
        for j in range(previousSeam[i][1]-1, -1, -1):
            # channels1 = np.linalg.norm(currentFrame[i][j])
            # channels2 = np.linalg.norm(currentFrame[i][j + 1])
            cumulativeCost += np.absolute(currentFrame[i][j] - currentFrame[i][j + 1])
            costMap[i][j] = cumulativeCost
        cumulativeCost = 0
        costMap[i][previousSeam[i][1]] = 0
        for j in range(previousSeam[i][1]+1, currentFrame.shape[1]):
            # channels1 = np.linalg.norm(currentFrame[i][j])
            # channels2 = np.linalg.norm(currentFrame[i][j - 1])
            cumulativeCost += np.absolute(currentFrame[i][j] - currentFrame[i][j - 1])
            costMap[i][j] = cumulativeCost
            
    return np.array(costMap, dtype=np.float64)

def retarget_video(videoIn, width, height, window, weights):
    video = videoIn.copy()
    widthDif = len(video[0][0]) - width
    heightDif = len(video[0]) - height

    # Shrink first
    if (widthDif > 0):
        for i in range(0, widthDif):
            print("Current Seam: ", i + 1)
            min_seam = None
            for j in range(0, len(video)):
                print("Current Frame: ", j+1)
                currentFrame = np.array(video[j])
                costMap = getPixelMeasures(currentFrame, window, weights, min_seam)
                cv2.imwrite("images/temp_costmap_" + str(i) + "." + str(j) + ".jpg", costMap)
                seam, energies = carve_seams_piecewise(costMap, window)
                min_index = np.where(energies[-1] == np.amin(energies[-1]))[0][::-1]
                min_index = min_index[0]
                min_seam = np.array(get_seam(seam, min_index))
                new_frame = remove_seam(currentFrame, seam, min_index)
                mask = highlight_seam(currentFrame, min_seam)
                cv2.imwrite("images/temp_seam_" + str(i) + "." + str(j) + ".jpg", mask)
                del video[j]
                video.insert(j, new_frame)
            write_video(video, "videos/temp" + str(i) + ".mp4", len(video[0][0]), len(video[0]))
    if (heightDif > 0):
        for i in range(0, heightDif):
            print("Current Seam: ", i + 1)
            min_seam = None
            for j in range(0, len(video)):
                print("Current Frame: ", j+1)
                currentFrame = np.array(video[j])
                currentFrame = np.transpose(currentFrame, axes=(1, 0, 2))
                costMap = getPixelMeasures(currentFrame, window, weights, min_seam)
                cv2.imwrite("images/temp_costmap_horiz" + str(i) + "." + str(j) + ".jpg", costMap)
                seam, energies = carve_seams_piecewise(costMap, window)
                min_index = np.where(energies[-1] == np.amin(energies[-1]))[0][::-1]
                min_index = min_index[0]
                min_seam = np.array(get_seam(seam, min_index))
                new_frame = remove_seam(currentFrame, seam, min_index)
                new_frame = np.transpose(new_frame, axes=(1, 0, 2))
                mask = highlight_seam(currentFrame, min_seam)
                cv2.imwrite("images/temp_seam_horizo" + str(i) + "." + str(j) + ".jpg", mask)
                del video[j]
                video.insert(j, new_frame)
            write_video(video, "videos/temp_horiz" + str(i) + ".mp4", len(video[0][0]), len(video[0]))
    # Then expand
    if (widthDif < 0):
        min_seam = None
        for j in range(0, len(video)):
            print("Current Frame: ", j+1)
            currentFrame = np.array(video[j])
            costMap = getPixelMeasures(currentFrame, window, weights, min_seam)
            cv2.imwrite("images/temp_costmap_expand" + str(j) + ".jpg", costMap)
            seam, energies = carve_seams_piecewise(costMap, window)
            newSeams, newEnergies = get_n_seams(seam, energies, abs(widthDif))
            min_index = np.where(energies[-1] == np.amin(energies[-1]))[0][::-1]
            min_index = min_index[0]
            min_seam = np.array(get_seam(seam, min_index))
            new_frame = add_seams(currentFrame, newSeams)
            mask = highlight_seam(currentFrame, min_seam)
            cv2.imwrite("images/temp_seam_expand" + str(j) + ".jpg", mask)
            del video[j]
            video.insert(j, new_frame)
            gc.collect()

    if (heightDif < 0):
        pass

    return video

# Weights is tuple of (SC:TC:S)
def getPixelMeasures(frameIn, spatialWindow, weights, previousSeam=None):
    frame = cv2.cvtColor(frameIn, cv2.COLOR_BGR2GRAY)
    frame = np.asarray(frame, dtype=int)
    spatialWeight, temporalWeight, saliencyWeight = weights / np.sum(weights)
    spatialMap = spatial_coherence.compute_spatial_coherence_cost(frame, spatialWindow)
    spatialMap = spatialMap * spatialWeight
    saliency = saliency_map(frame)
    saliency = saliency * saliencyWeight
    if (previousSeam is None):
        return (spatialMap + saliency) / np.max(spatialMap + saliency) * 255
    temporalMap = compute_temporal_coherence_cost(frame, previousSeam)
    temporalMap = temporalMap * temporalWeight
    return (spatialMap + saliency + temporalMap) / np.max(spatialMap + saliency + temporalMap) * 255

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Retargets a video to specified size")
    parser.add_argument('--video', type=str, help='The path to the video to retarget')
    parser.add_argument('--width', type=int, help='Width to retarget video to')
    parser.add_argument('--height', type=int, help='Height to retarget video to')
    parser.add_argument('--out', type=str, help='The path to store the output to')
    parser.add_argument('--window', type=int, help='Window for piecewise seams', default=10)
    parser.add_argument('--saliencyW', type=float, help='Saliency Weight in seam carving', default=2)
    parser.add_argument('--spatialW', type=float, help='Spatial Weight in seam carving', default=5)
    parser.add_argument('--temporalW', type=float, help='Temporal Weight in seaming carving', default=0.5)


    args = parser.parse_args()

    print("INFO: Reading Video: ", args.video)
    video = read_video(args.video)
    print("INFO: Finished Reading Video")
    newVideo = retarget_video(video, args.width, args.height, args.window, (args.spatialW, args.temporalW, args.saliencyW))
    print("INFO: Writing Output Video")
    write_video(newVideo, args.out, args.width, args.height)

    #
    # print("INFO: Calculating Saliency Map")
    # saliency_frame = saliency_map(video[120])
    #
    # print("INFO: Calculating Seams")
    # seam, energies = carve_seams(saliency_frame)
    # print("INFO: Finished Calculating Seams")
    # min_index = np.where(energies == np.amin(energies))[0][::-1]
    # min_index = min_index[0]
    # min_seam = seam[min_index]
    #
    # mask = highlight_seam(video[120], min_seam)
    # print("INFO: Saving New Image")
    # cv2.imwrite("saliency_seam_demo.jpg", mask)
    #
    # print("INFO: Calculating Temporal Cost to Next Frame")
    # temporal_map = compute_temporal_coherence_cost(video[121], min_seam)
    # temporal_map = temporal_map / np.max(temporal_map) * 255
    # cv2.imwrite("temporal_demo.jpg", temporal_map)
    # #print("INFO: Saving New Image")
    # #cv2.imwrite("temporal_map_demo.jpg", temporal_map.astype(np.uint8))
    #
    # print("INFO: Calculating Merged Maps")
    # costMap = getPixelMeasures(video[121], 15, (5, 1, 2), min_seam)
    # cv2.imwrite("cost_map_demo.jpg", costMap)
    #
    # print("INFO: Calculating Seams from Temporal Cost")
    # seam2, energies2 = carve_seams_piecewise(costMap, 3)
    # min_index2 = np.where(energies2 == np.amin(energies2))[0][::-1]
    # min_index2 = min_index2[0]
    # min_seam2 = seam2[min_index]
    #
    # mask2 = highlight_seam(video[121], min_seam2)
    # print("INFO: Saving New Image")
    # cv2.imwrite("cost_seam_demo.jpg", mask2)

    #write_video(video, args.out)
