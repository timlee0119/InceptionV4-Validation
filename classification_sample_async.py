#!/usr/bin/env python
"""
 Copyright (C) 2018-2019 Intel Corporation

 Licensed under the Apache License, Version 2.0 (the "License");
 you may not use this file except in compliance with the License.
 You may obtain a copy of the License at

      http://www.apache.org/licenses/LICENSE-2.0

 Unless required by applicable law or agreed to in writing, software
 distributed under the License is distributed on an "AS IS" BASIS,
 WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 See the License for the specific language governing permissions and
 limitations under the License.
"""
from __future__ import print_function
import sys
import os
from argparse import ArgumentParser, SUPPRESS
import cv2
import numpy as np
import logging as log
from time import time
from openvino.inference_engine import IENetwork, IECore
import threading
import imageio

class InferReqWrap:
    def __init__(self, request, id, num_iter):
        self.id = id
        self.request = request
        self.num_iter = num_iter
        self.cur_iter = 0
        self.cv = threading.Condition()
        self.request.set_completion_callback(self.callback, self.id)

    def callback(self, statusCode, userdata):
        if (userdata != self.id):
            log.error("Request ID {} does not correspond to user data {}".format(self.id, userdata))
        elif statusCode != 0:
            log.error("Request {} failed with status code {}".format(self.id, statusCode))
        self.cur_iter += 1
        log.info("Completed {} Async request execution".format(self.cur_iter))
        if self.cur_iter < self.num_iter:
            # here a user can read output containing inference results and put new input
            # to repeat async request again
            self.request.async_infer(self.input)
        else:
            # continue sample execution after last Asynchronous inference request execution
            self.cv.acquire()
            self.cv.notify()
            self.cv.release()

    def execute(self, mode, input_data):
        if (mode == "async"):
            log.info("Start inference ({} Asynchronous executions)".format(self.num_iter))
            self.input = input_data
            # Start async request for the first time. Wait all repetitions of the async request
            self.request.async_infer(input_data)
            self.cv.acquire()
            self.cv.wait()
            self.cv.release()
        elif (mode == "sync"):
            log.info("Start inference ({} Synchronous executions)".format(self.num_iter))
            for self.cur_iter in range(self.num_iter):
                # here we start inference synchronously and wait for
                # last inference request execution
                self.request.infer(input_data)
                log.info("Completed {} Sync request execution".format(self.cur_iter + 1))
        else:
            log.error("wrong inference mode is chosen. Please use \"sync\" or \"async\" mode")
            sys.exit(1)



def build_argparser():
    parser = ArgumentParser(add_help=False)
    args = parser.add_argument_group('Options')
    args.add_argument('-h', '--help', action='help', default=SUPPRESS, help='Show this help message and exit.')
    args.add_argument("-m", "--model", help="Required. Path to an .xml file with a trained model.",
                      required=True, type=str)
    args.add_argument("-i", "--input", help="Required. Path to a folder with images or path to an image files",
                      required=True, type=str, nargs="+")
    args.add_argument("-o", "--output", help="Required. Path to a .json file with inference results",
                      required=True, type=str)
    args.add_argument("-b", "--batch", help="Required. Inference batch size",
                      required=True, type=int)
    args.add_argument("-l", "--cpu_extension",
                      help="Optional. Required for CPU custom layers. Absolute path to a shared library with the"
                           " kernels implementations.", type=str, default=None)
    args.add_argument("-d", "--device",
                      help="Optional. Specify the target device to infer on; CPU, GPU, FPGA, HDDL or MYRIAD is "
                           "acceptable. The sample will look for a suitable plugin for device specified. Default value is CPU",
                      default="CPU", type=str)
    args.add_argument("--labels", help="Optional. Labels mapping file", default=None, type=str)
    args.add_argument("-nt", "--number_top", help="Optional. Number of top results", default=10, type=int)

    return parser

def main():
    log.basicConfig(format="[ %(levelname)s ] %(message)s", level=log.INFO, stream=sys.stdout)
    args = build_argparser().parse_args()
    model_xml = args.model
    model_bin = os.path.splitext(model_xml)[0] + ".bin"

    # Plugin initialization for specified device and load extensions library if specified
    log.info("Creating Inference Engine")
    ie = IECore()
    if args.cpu_extension and 'CPU' in args.device:
        ie.add_extension(args.cpu_extension, "CPU")
    # Read IR
    log.info("Loading network files:\n\t{}\n\t{}".format(model_xml, model_bin))
    net = IENetwork(model=model_xml, weights=model_bin)

    if "CPU" in args.device:
        supported_layers = ie.query_network(net, "CPU")
        not_supported_layers = [l for l in net.layers.keys() if l not in supported_layers]
        if len(not_supported_layers) != 0:
            log.error("Following layers are not supported by the plugin for specified device {}:\n {}".
                      format(args.device, ', '.join(not_supported_layers)))
            log.error("Please try to specify cpu extensions library path in sample's command line parameters using -l "
                      "or --cpu_extension command line argument")
            sys.exit(1)
    assert len(net.inputs.keys()) == 1, "Sample supports only single input topologies"
    # print(net.outputs)
    # assert len(net.outputs) == 1, "Sample supports only single output topologies"

    log.info("Preparing input blobs")
    input_blob = next(iter(net.inputs))
    out_blob = next(iter(net.outputs))
    out_blob = 'InceptionV4/Logits/Predictions'
    net.batch_size = min(args.batch, len(args.input))

    # Read and pre-process input images
    n, c, h, w = net.inputs[input_blob].shape
    log.info("Total input images: %d" % len(args.input))
    
    # Loading model to the plugin
    log.info("Loading model to the plugin")
    exec_net = ie.load_network(network=net, device_name=args.device)
    
    # execute inference by batch
    output_json = {}
    fps_hist = []
    latency_hist = []
    for start in range(0, len(args.input), args.batch):
        log.info("Start batch: %d" % (start/args.batch + 1))
        input_subset = args.input[start:start+args.batch]
        images = np.ndarray(shape=(len(input_subset), c, h, w))
        
        load_img_start = time()
        for i in range(start, start + len(input_subset)):
            image = cv2.imread(args.input[i])
            # handle .gif
            if image is None:
                # log.warning("{} cv2.imread failed. Try imageio.mimread.".format(args.input[i]))
                tmp = imageio.mimread(args.input[i])
                assert tmp is not None, "Neither cv2 nor imageio can read this file: {}".format(args.input[i])
                image = np.array(tmp)
                if image.ndim == 3:
                    image = np.stack((image,)*3, axis=-1)
                image = image[0][:,:,0:3]
                
            if image.shape[:-1] != (h, w):
                # log.warning("Image {} is resized from {} to {}".format(args.input[i], image.shape[:-1], (h, w)))
                image = cv2.resize(image, (w, h))
            image = image.transpose((2, 0, 1))  # Change data layout from HWC to CHW
            images[i-start] = image
        log.info("Batch size is {}".format(len(input_subset)))
        
        # create one inference request for asynchronous execution
        request_id = 0
        infer_request = exec_net.requests[request_id];

        num_iter = 1
        request_wrap = InferReqWrap(infer_request, request_id, num_iter)        
        
        # Start inference request execution. Wait for last execution being completed
        inf_start = time()
        request_wrap.execute("sync", {input_blob: images})
        
        # Processing output blob
        # log.info("Processing output blob")
        res = infer_request.outputs[out_blob]
        inf_end = time()
        inf_time = inf_end - inf_start
        latency = inf_end - load_img_start
        fps_hist.append(1.0 / inf_time)
        latency_hist.append(latency)
        
        # log.info("Top {} results: ".format(args.number_top))
        if args.labels:
            with open(args.labels, 'r') as f:
                labels_map = [x.split(sep=' ', maxsplit=1)[-1].strip() for x in f]
        else:
            labels_map = None
        classid_str = "classid"
        probability_str = "probability"

        for i, probs in enumerate(res):
            probs = np.squeeze(probs)
            output_json[input_subset[i].split('/')[-1]] = [str(x) for x in probs[1:]]
            '''
            top_ind = np.argsort(probs)[-args.number_top:][::-1]
            print("Image {}\n".format(input_subset[i]))
            print(classid_str, probability_str)
            print("{} {}".format('-' * len(classid_str), '-' * len(probability_str)))
            for id in top_ind:
                det_label = labels_map[id] if labels_map else "{}".format(id)
                label_length = len(det_label)
                space_num_before = (7 - label_length) // 2
                space_num_after = 7 - (space_num_before + label_length) + 2
                space_num_before_prob = (11 - len(str(probs[id]))) // 2
                print("{}{}{}{}{:.7f}".format(' ' * space_num_before, det_label,
                                              ' ' * space_num_after, ' ' * space_num_before_prob,
                                              probs[id]))
            print("\n")      
            '''      
     
    import json
    tmp = json.dumps(output_json)
    with open(args.output, 'w') as outfile:
        outfile.write(tmp)
    log.info("Writing inference results to {}".format(args.output))
    
    print("Average FPS: %f" % (sum(fps_hist) / len(fps_hist)))
    print("Average latency: %f ms" % (sum(latency_hist) / len(latency_hist) * 1000))


if __name__ == '__main__':
    sys.exit(main() or 0)
