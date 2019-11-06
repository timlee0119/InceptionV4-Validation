# InceptionV4-Validation
## How to get the inceptionV4 model:
1. Download .ckpt checkpoint file from: 
https://docs.openvinotoolkit.org/latest/_docs_MO_DG_prepare_model_convert_model_Convert_Model_From_TensorFlow.html
2. Follow the instruction to export .pb inference graph from .ckpt file:
https://github.com/tensorflow/models/blob/master/research/slim/README.md#exporting-the-inference-graph
3. Convert the model to IR format (.xml + .bin)
