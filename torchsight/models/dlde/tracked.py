"""First version of the DLDENet with tracked means.

Deep Local Directional Embedding (DLDE) module.

This net is based on the RetinaNet architecture but provides a different submodule
for classification and additional methods.
"""
import torch
from torch import nn

from ..retinanet import RetinaNet, SubModule
from ..anchors import Anchors


class DirectionalClassification(nn.Module):
    """Directional classification module.

    This module takes the features generated by the Feature Pyramid Network and generates
    "embeddings" (normalized vectors for each base anchor) that must point in the same direction
    that its correct label and so it can calculate the probability of being for a given class.

    So, in simple words, we take a picture and project each section of the image to a sphere with
    unit radius. Each section has an embedding, a vector that points in some direction that it is
    in this sphere.

    What we are going to do? We are going to try that embeddings of similar objects (i.e. same class)
    point to the same direction. It would be an error that the embeddings point to the exact same
    direction, we must have a threshold, so we can model this with a Von Mises-Fisher distribution.

    At this point we need a picture, check this and create your mental image:
    ![](https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcTDBP4M7VABT1wGuXccdg707MzyQPTpb5O6D3TUCZFapDBG_jiX)

    So, we want that semantically similar objects points to similar directions, so the direction of the
    embedding contains the semantic of the object without losing much visual detail.

    I was inspired by the following paper:

    _Directional Statistics-based Deep Metric Learning for Image Classification and Retrieval._
    [Paper in Arxiv](https://arxiv.org/abs/1802.09662)

    But there is a problem: If we use only this approach we use a thing similar to a softmax but over the
    cosine similarity of the embedding with each class' mean, as the softmax always gives a winner
    this won't allow us to identify correctly a background embedding, i.e., a non interesting object
    for the model.

    To avoid this, we can use the sigmoid function instead of the softmax. But if we only use
    the cosine similarity and the sigmoid function it could be impossible to have a precision of 1
    (because sigmoid(maximum cosine similarity = 1) = 0.7311).
    So we must add a weight and a bias to modify the sigmoid and get coherent values.

    How can it update the means?
    Because every time that the embeddings pass through the network we must provide the real annotations
    to update the classes' means with those embeddings. Not a fully update of course, we sum this embeddings
    to the previously seen embeddings and then you can call model.update_means() to normalize those sums
    and set them as the new means for each class.

    Obviously, a correct way to set a more precise mean is to call update_means() after each epoch,
    because it will compute the mean with the average of the class' embeddings.
    """

    def __init__(self, in_channels, embedding_size, anchors, features, classes, assignation_thresholds):
        """Initialize the module.

        Arguments:
            in_channels (int): The number of channels of the feature map.
            embedding_size (int): Length of the embedding vector to generate.
            anchors (int): Number of anchors per location in the feature map.
            features (int): Number of features in the conv layers that generates the embedding.
            classes (int): The number of classes to detect.
            assignation_thresholds (dict): A dict with the thresholds to assign an anchor to an object
                or to background. It must have the keys 'object' and 'background' with float values.
        """
        super(DirectionalClassification, self).__init__()

        if 'object' not in assignation_thresholds:
            raise ValueError('There is no "object" threshold in the assignation threshold dict')
        if 'background' not in assignation_thresholds:
            raise ValueError('There is no "background" threshold in the assignation threshold dict')

        self.assignation_thresholds = assignation_thresholds
        self.embedding_size = embedding_size
        self.sigmoid = nn.Sigmoid()

        # Start the means for the distributions as zero vectors
        # We can get the mean for the i-th class with self.means[i]
        self.register_buffer('means', torch.zeros(classes, embedding_size))
        self.weight = nn.Parameter(torch.Tensor(classes))
        self.bias = nn.Parameter(torch.Tensor(classes))
        # A start weight of 4 to get values between [0, 1] in the range [-1, 1] for the cosine similarity
        nn.init.constant_(self.weight, 4)
        nn.init.constant_(self.bias, 0)

        # We need to keep track of embeddings for each class to update the means. How? The mean could be
        # calculated by the average of the embeddings of the same class normalized. So it's the sum of
        # embeddings that passes through the network and that result normalized to have unit norm.
        self.register_buffer('embeddings_sums', torch.zeros_like(self.means))

        # Create the encoder
        self.encoder = SubModule(in_channels=in_channels, outputs=embedding_size,
                                 anchors=anchors, features=features)

    def encode(self, feature_map):
        """Generate the embeddings for the given feature map.

        Arguments:
            feature_map (torch.Tensor): The feature map to use to generate the embeddings.
                Shape:
                    (batch size, number of features, feature map's height, width)

        Returns:
            torch.Tensor: The embedding for each anchor for each location in the feature map.
                Shape:
                    (batch size, number of total anchors, embedding size)
        """
        batch_size = feature_map.shape[0]
        # Shape (batch size, number of anchors per location * embedding size, height, width)
        embeddings = self.encoder(feature_map)
        # Move the embeddings to the last dimension
        embeddings = embeddings.permute(0, 2, 3, 1).contiguous()
        # Shape (batch size, number of total anchors, embedding size)
        embeddings = embeddings.view(batch_size, -1, self.embedding_size)
        # Normalize the embeddings
        return embeddings / embeddings.norm(dim=2, keepdim=True)

    def track(self, embeddings, anchors, annotations):
        """Track the embeddings to accumulate the embeddings assigned to the same class.

        Take the embeddings, assign the annotations to each anchor get the assigned anchors
        to objects and with those embeddings sum to the embeddings_sums according with their
        assignations to annotations.

        This way we can track all the embeddings per class and then call update_means()
        to set the new means of the model.

        Also, to avoid conflicts with different amount of annotations per image, this method
        assumes that there could be *fake annotations* labeled with -1. So if the last value
        of an annotation is -1 this method does not take that annotation.

        Arguments:
            embeddings (torch.Tensor): All the embeddings generated for each image in the batch.
                Shape:
                    (batch size, number of total anchors, embedding size)
            anchors (torch.Tensor): The base anchors for each location in the feature map.
                Shape:
                    (batch size, number of total anchors, 4)
            annotations (torch.Tensor): The ground truth annotations for the images.
                It assumes that each annotation contains the label in the last value.
                Shape:
                    (batch size, number of annotations, 5)
        """
        with torch.no_grad():
            # As each image could have different amount of annotations we must iterate and remove false annotations
            for index, current_annotations in enumerate(annotations):
                current_anchors = anchors[index]
                current_embeddings = embeddings[index]
                # Remove false annotations that have -1 label
                mask = current_annotations[:, -1] != -1
                current_annotations = current_annotations[mask]
                # Get the assigned annotation for each anchor and which anchors are assigned as objects
                assignations = Anchors.assign(current_anchors, current_annotations,
                                              thresholds=self.assignation_thresholds)
                assigned_annotations, objects_mask, *_ = assignations
                # Track only the assigned to objects embeddings
                objects_embeddings = current_embeddings[objects_mask]
                objects_annotations = assigned_annotations[objects_mask]
                # Iterate over the labels of the annotations, accumulate the embeddings with the same label assigned
                # and sum them to the embeddings_sum
                for label in assigned_annotations[:, -1].unique():
                    mask = objects_annotations[:, -1] == label
                    self.embeddings_sums[label.type(torch.long)] += objects_embeddings[mask].sum(dim=0)

    def classify(self, embeddings):
        """Get the probability for each embedding to below to each class.

        Compute the cosine similarity between each embedding and each class' mean and return
        the modified sigmoid applied over the similarities to get probabilities.

        Arguments:
            embeddings (torch.Tensor): All the embeddings generated.
                Shape:
                    (batch size, total embeddings per image, embedding size)

        Returns:
            torch.Tensor: The probabilities for each embedding.
                Shape:
                    (batch size, total embeddings, number of classes)
        """
        similarity = torch.matmul(embeddings, self.means.permute(1, 0))
        return self.sigmoid(self.weight * (similarity + self.bias))

    def forward(self, feature_maps, anchors=None, annotations=None, classify=True):
        """Update means and get the probabilities for each embedding to belong to each class.

        It needs the annotations and the anchors to keep track of the mean for each class.
        If you only want to get the probabilities for each class it does not need the annotations nor anchors.

        Arguments:
            feature_maps (torch.Tensor): Feature maps generated by the FPN module.
                Shape:
                    (batch size, channels, height, width)
            anchors (torch.Tensor): The base anchors for each location in the feature map.
                Shape:
                    (batch size, number of total anchors, 4)
            annotations (torch.Tensor, optional): The annotations of the image. Useful to keep track
                of the mean for each class. It assumes that each annotation contains the label in the
                last value.
                Shape:
                    (batch size, number of annotations, 5)
            classify (bool, optional): Indicates if it must return the classification probs.

        Returns:
            torch.Tensor: Tensor with the probability for each anchor to belong to each class.
                Shape:
                    (batch size, feature map's height * width * number of anchors, classes)
        """
        embeddings = torch.cat([self.encode(feature_map) for feature_map in feature_maps], dim=1)

        if self.training:
            if anchors is None:
                raise ValueError('Training mode: Directional classification cannot train without the base anchors')
            if annotations is None:
                raise ValueError('Training mode: Directional classification cannot train without the annotations')

            self.track(embeddings, anchors, annotations)

        # Compute the probabilities
        if classify:
            return self.classify(embeddings)

    def update_means(self):
        """Normalize the embeddings_sums and set them as the new means for the module."""
        with torch.no_grad():
            self.means = self.embeddings_sums / self.embeddings_sums.norm(dim=1, keepdim=True)


class DLDENetWithTrackedMeans(RetinaNet):
    """Deep Local Directional Embeddings Net.

    Based on RetinaNet, for more info about the architecture please visit the RetinaNet documentation.
    """

    def __init__(self, classes, resnet=18, features=None, anchors=None, embedding_size=512,
                 assignation_thresholds=None, pretrained=True, device=None):
        """Initialize the network.

        Arguments:
            classes (int): The number of classes to detect.
            resnet (int, optional): The depth of the resnet backbone for the Feature Pyramid Network.
            features (dict, optional): The dict that indicates the features for each module of the network.
                For the default dict please see RetinaNet module.
            anchors (dict, optional): The dict with the 'sizes', 'scales' and 'ratios' sequences to initialize
                the Anchors module. For default values please see RetinaNet module.
            embedding_size (int, optional): The length of the embedding to generate per anchor.
            assignation_thresholds (dict): A dict with the thresholds to assign an anchor to an object
                or to background. It must have the keys 'object' and 'background' with float values.
            pretrained (bool, optional): If the resnet backbone of the FPN must be pretrained on the ImageNet dataset.
                This pretraining is provided by the torchvision package.
            device (str, optional): The device where the module will run.
        """
        self.embedding_size = embedding_size

        if assignation_thresholds is not None:
            self.assignation_thresholds = assignation_thresholds
        else:
            self.assignation_thresholds = {'object': 0.5, 'background': 0.4}

        super().__init__(classes, resnet, features, anchors, pretrained, device)

    def get_classification_module(self, in_channels, classes, anchors, features):
        """Get the directional classification module.

        See __init__ method in RetinaNet class for more information.

        Arguments:
            in_channels (int): The number of channels of the feature map.
            classes (int): Indicates the number of classes to predict.
            anchors (int, optional): The number of anchors per location in the feature map.
            features (int, optional): Indicates the number of inner features that the conv layers must have.

        Returns:
            DirectionalClassification: The module for classification.
        """
        return DirectionalClassification(in_channels=in_channels, embedding_size=self.embedding_size,
                                         anchors=anchors, features=features, classes=classes,
                                         assignation_thresholds=self.assignation_thresholds)

    def forward(self, images, annotations=None, initializing=False):
        """Forward pass through the network.

        ----- Training -----

        In training mode (model.train()) returns the base anchors, the regressions for those anchors
        and the classification probabilities for each anchor.

        During training is mandatory to provide the annotations to keep track of the means for each class'
        direction. As each image could have different amounts of annotations to fit them in only one tensor
        you can fill the 'false' annotations with -1 and so those annotations will be ignored.

        Example: You have 2 images, the first one with only 1 annotation and the second one with 3.
        How could you fit them in only one tensor for the batch? You know that the maximum amount
        of annotations is 3 so you could generate a tensor with shape (2, 3, 5): 2 images, 3 'maximum
        amount of annotations' and 5 values for each annotation. But what are the values for the
        tensors at [0, 1, :] and [0, 2, :]? There are no annotations to provide, so you can fill them
        with -1 to indicate that those are 'false' annotations.

        ----- Evaluation -----

        In evaluation mode (calling `model.eval()`) it applies Non-Maximum Supresion to keep only
        the predictions that do not collide.

        On evaluation mode we cannot return only two tensors (bounding boxes and classifications) because
        different images could have different amounts of predictions over the threshold so we cannot keep
        all them in a single tensor.
        To avoid this problem in evaluation mode it returns a sequence of (bounding_boxes, classifications)
        for each image.

        Arguments:
            images (torch.Tensor): The batch of images to pass through the network.
                Shape:
                    (batch size, channels, height, width)
            annotations (torch.Tensor, optional): The annotations for the batch. Each annotation
                must have x1, y1 (top left corner), x2, y2 (bottom right corner) and label for the class.
                Shape:
                    (batch size, maximum annotations length, 5)
            initializing (bool, optional): Indicates that the forward pass is to initialize the means of the
                classification submodule.
        """
        # Get the feature maps for the images
        feature_maps = self.fpn(images)
        # Get the base anchors
        anchors = self.anchors(images)

        if initializing:
            return self.classification(feature_maps, anchors, annotations, classify=False)

        # Get the probabilities for each class
        classifications = self.classification(feature_maps, anchors, annotations)
        # Get the regression values for the images
        regressions = torch.cat([self.regression(feature_map) for feature_map in feature_maps], dim=1)

        if self.training:
            return anchors, regressions, classifications
        if self.evaluate_loss:
            return anchors.detach(), regressions.detach(), classifications.detach()

        return self.transform(images, anchors, regressions, classifications)

    @classmethod
    def from_checkpoint(cls, checkpoint, device=None):
        """Get an instance of the model from a checkpoint generated with the DLDENetWithTrackedMeansTrainer.

        Arguments:
            checkpoint (str or dict): The path to the checkpoint file or the loaded checkpoint file.
            device (str, optional): The device where to load the model.

        Returns:
            DLDENet: An instance with the weights and hyperparameters got from the checkpoint file.
        """
        device = device if device is not None else 'cuda:0' if torch.cuda.is_available() else 'cpu'

        if isinstance(checkpoint, str):
            checkpoint = torch.load(checkpoint, map_location=device)

        params = checkpoint['hyperparameters']['model']

        model = cls(classes=params['classes'],
                    resnet=params['resnet'],
                    features=params['features'],
                    anchors=params['anchors'],
                    embedding_size=params['embedding_size'],
                    assignation_thresholds=params['assignation_thresholds'],
                    pretrained=params['pretrained'])
        model.load_state_dict(checkpoint['model'])
        model.classification.embeddings_sums = torch.zeros_like(model.classification.means).to(device)

        return model
