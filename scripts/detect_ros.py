#!/usr/bin/env python
## Author: Rohit
## Date: July, 25, 2017
#Purpose: Ros node to detect objects using tensorflow

import numpy as np
import os
import six.moves.urllib as urllib
import sys
import tarfile
try:
    import tensorflow as tf
except ImportError:
    print("unable to import TensorFlow. Is it installed?")
    print("  sudo apt install python-pip")
    print("  sudo pip install tensorflow")
    sys.exit(1)
import zipfile
import cv2
import object_detection
from collections import defaultdict
from io import StringIO
import matplotlib
from matplotlib import pyplot as plt

#for ros:
import rospy
from std_msgs.msg import String
from std_msgs.msg import Header
from sensor_msgs.msg import Image
from cv_bridge import CvBridge, CvBridgeError
from vision_msgs.msg import Detection2D, Detection2DArray, ObjectHypothesisWithPose
import copy
import rospkg
rospack = rospkg.RosPack()

#for time
import time

from tensorflow.core.framework import graph_pb2

# ## Object detection imports
# Here are the imports from the object detection module.
from object_detection.utils import label_map_util

from object_detection.utils import visualization_utils as vis_util


# What model to use
######### CHANGE THE MODEL NAME HERE ############
MODEL_NAME =  'ssd_mobilenet_v1_coco_2017_11_17'
# By default models are stored in data/models/
MODEL_PATH = os.path.join(rospack.get_path('tensorflow_object_detector'),'data','models' , MODEL_NAME)

# Path to frozen detection graph. This is the actual model that is used for the object detection.
PATH_TO_CKPT = MODEL_PATH + '/frozen_inference_graph.pb'
######### CHANGE THE LABEL NAME HERE ###########
LABEL_NAME = 'mscoco_label_map.pbtxt'
# List of the strings that is used to add correct label for each box.
PATH_TO_LABELS = os.path.join(rospack.get_path('tensorflow_object_detector'),'data','labels', LABEL_NAME)

###CHANGE NUMBER OF CLASSES HERE ####
NUM_CLASSES = 90

def _node_name(n):
  if n.startswith("^"):
    return n[1:]
  else:
    return n.split(":")[0]

input_graph = tf.Graph()
with tf.Session(graph=input_graph):
    score = tf.placeholder(tf.float32, shape=(None, 1917, 90), name="Postprocessor/convert_scores")
    expand = tf.placeholder(tf.float32, shape=(None, 1917, 1, 4), name="Postprocessor/ExpandDims_1")
    for node in input_graph.as_graph_def().node:
        if node.name == "Postprocessor/convert_scores":
            score_def = node
        if node.name == "Postprocessor/ExpandDims_1":
            expand_def = node

detection_graph = tf.Graph()
with detection_graph.as_default():
  od_graph_def = tf.GraphDef()
  with tf.gfile.GFile(PATH_TO_CKPT, 'rb') as fid:
    serialized_graph = fid.read()
    od_graph_def.ParseFromString(serialized_graph)
    dest_nodes = ['Postprocessor/convert_scores','Postprocessor/ExpandDims_1']

    edges = {}
    name_to_node_map = {}
    node_seq = {}
    seq = 0
    for node in od_graph_def.node:
      n = _node_name(node.name)
      name_to_node_map[n] = node
      edges[n] = [_node_name(x) for x in node.input]
      node_seq[n] = seq
      seq += 1

    for d in dest_nodes:
      assert d in name_to_node_map, "%s is not in graph" % d

    nodes_to_keep = set()
    next_to_visit = dest_nodes[:]
    while next_to_visit:
      n = next_to_visit[0]
      del next_to_visit[0]
      if n in nodes_to_keep:
        continue
      nodes_to_keep.add(n)
      next_to_visit += edges[n]

    nodes_to_keep_list = sorted(list(nodes_to_keep), key=lambda n: node_seq[n])

    nodes_to_remove = set()
    for n in node_seq:
      if n in nodes_to_keep_list: continue
      nodes_to_remove.add(n)
    nodes_to_remove_list = sorted(list(nodes_to_remove), key=lambda n: node_seq[n])

    keep = graph_pb2.GraphDef()
    for n in nodes_to_keep_list:
      keep.node.extend([copy.deepcopy(name_to_node_map[n])])

    remove = graph_pb2.GraphDef()
    remove.node.extend([score_def])
    remove.node.extend([expand_def])
    for n in nodes_to_remove_list:
      remove.node.extend([copy.deepcopy(name_to_node_map[n])])

    with tf.device('/gpu:0'):
      tf.import_graph_def(keep, name='')
    with tf.device('/cpu:0'):
      tf.import_graph_def(remove, name='')

label_map = label_map_util.load_labelmap(PATH_TO_LABELS)
categories = label_map_util.convert_label_map_to_categories(label_map, max_num_classes=NUM_CLASSES, use_display_name=True)
category_index = label_map_util.create_category_index(categories)

def load_image_into_numpy_array(image):
  (im_width, im_height) = image.size
  return np.array(image.getdata()).reshape(
      (im_height, im_width, 3)).astype(np.uint8)

# # Detection
tf_config = tf.ConfigProto()
tf_config.allow_soft_placement = True
tf_config.gpu_options.allow_growth = True


with detection_graph.as_default():
  with tf.Session(graph=detection_graph,config=tf_config) as sess:
    class detector:

      def __init__(self):
        self.image_pub = rospy.Publisher("debug_image",Image, queue_size=1)
        self.object_pub = rospy.Publisher("objects", Detection2DArray, queue_size=1)
        self.bridge = CvBridge()
        self.image_sub = rospy.Subscriber("image", Image, self.image_cb, queue_size=1, buff_size=2**24)

      def image_cb(self, data):
        objArray = Detection2DArray()
        try:
          cv_image = self.bridge.imgmsg_to_cv2(data, "bgr8")
        except CvBridgeError as e:
          print(e)
        image=cv2.cvtColor(cv_image,cv2.COLOR_BGR2RGB)

        # the array based representation of the image will be used later in order to prepare the
        # result image with boxes and labels on it.
        image_np = np.asarray(image)
        # Expand dimensions since the model expects images to have shape: [1, None, None, 3]
        image_np_expanded = np.expand_dims(image_np, axis=0)

        image_tensor = detection_graph.get_tensor_by_name('image_tensor:0')
    	score_out = detection_graph.get_tensor_by_name('Postprocessor/convert_scores:0')
    	expand_out = detection_graph.get_tensor_by_name('Postprocessor/ExpandDims_1:0')
    	score_in = detection_graph.get_tensor_by_name('Postprocessor/convert_scores_1:0')
    	expand_in = detection_graph.get_tensor_by_name('Postprocessor/ExpandDims_1_1:0')
    	detection_boxes = detection_graph.get_tensor_by_name('detection_boxes:0')
    	detection_scores = detection_graph.get_tensor_by_name('detection_scores:0')
    	detection_classes = detection_graph.get_tensor_by_name('detection_classes:0')
    	num_detections = detection_graph.get_tensor_by_name('num_detections:0')

	(score, expand) = sess.run([score_out, expand_out], feed_dict={image_tensor: image_np_expanded})
	(boxes, scores, classes, num) = sess.run([detection_boxes, detection_scores, detection_classes, 	   		num_detections], feed_dict={score_in:score, expand_in: expand})

        objects=vis_util.visualize_boxes_and_labels_on_image_array(
            image,
            np.squeeze(boxes),
            np.squeeze(classes).astype(np.int32),
            np.squeeze(scores),
            category_index,
            use_normalized_coordinates=True,
            line_thickness=2)

        objArray.detections =[]
        objArray.header=data.header
        # print len(objects)
        object_count=1

        for i in range(len(objects)):
          # print ("Object Number: %d "  %object_count)
          object_count+=1
          objArray.detections.append(self.object_predict(objects[i],data.header,image_np,cv_image))

        self.object_pub.publish(objArray)

        img=cv2.cvtColor(image_np, cv2.COLOR_BGR2RGB)
        image_out = Image()
        try:
          image_out = self.bridge.cv2_to_imgmsg(img,"bgr8")
        except CvBridgeError as e:
          print(e)
        image_out.header = data.header
        self.image_pub.publish(image_out)

      def object_predict(self,object_data, header, image_np,image):
        image_height,image_width,channels = image.shape
        obj=Detection2D()
        obj_hypothesis= ObjectHypothesisWithPose()

        object_id=object_data[0]
        object_score=object_data[1]
        dimensions=object_data[2]

        obj.header=header
        obj_hypothesis.id = object_id
        obj_hypothesis.score = object_score
        obj.results.append(obj_hypothesis)
        obj.bbox.size_y = int((dimensions[2]-dimensions[0])*image_height)
        obj.bbox.size_x = int((dimensions[3]-dimensions[1] )*image_width)
        obj.bbox.center.x = int((dimensions[1] + dimensions [3])*image_height/2)
        obj.bbox.center.y = int((dimensions[0] + dimensions[2])*image_width/2)

        return obj

def main(args):

  rospy.init_node('detector_node')
  obj=detector()

  try:
    rospy.spin()
  except KeyboardInterrupt:
    print("ShutDown")
  cv2.destroyAllWindows()

if __name__=='__main__':
  main(sys.argv)
