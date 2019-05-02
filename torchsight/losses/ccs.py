"""Implementation of the Classification vector-centered Cosine Similarity from the paper
[One-shot Face Recognition by Promoting Underrepresented Classes](https://arxiv.org/pdf/1707.05574.pdf).
"""
import torch
from torch import nn

from ..models import Anchors


class CCSLoss(nn.Module):
    """Classification vector-centered Cosine Similarity Loss.

    As indicated in the equation 5 of the paper, this loss tries to minimize the angular distance
    between the embeddings (or features before the classification) and the weighted vector that does
    the classification.

    This is done by simply doing a dot product between the embedding and the classification vector
    and normalizing by their norms.

    It will apply this loss term only to those embeddings that are assigned to an object.
    """

    def __init__(self, iou_thresholds, device=None):
        """Initialize the loss.

        Arguments:
            iou_thresholds (dict): Indicates the thresholds to assign an anchor as background or object.
            device (str, optional): Indicates the device where to run the loss.
        """
        super().__init__()

        if iou_thresholds is None:
            iou_thresholds = {'background': 0.4, 'object': 0.5}
        self.iou_thresholds = iou_thresholds
        self.device = device if device is not None else 'cuda:0' if torch.cuda.is_available() else 'cpu'

    def forward(self, anchors, embeddings, weights, annotations):
        """Get the mean CCS loss.

        Arguments:
            anchors (torch.Tensor): The base anchors (without the transformation to adjust the
                bounding boxes).
                Shape:
                    (batch size, total boxes, 4)
            embeddings (torch.Tensor): The embeddings generated for each anchor.
                Shape:
                    (batch size, number of anchors, embedding size)
            annotations (torch.Tensor): Ground truth. Tensor with the bounding boxes and the label for
                the object. The values must be x1, y1 (top left corner), x2, y2 (bottom right corner)
                and the last value is the label.
                Shape:
                    (batch size, maximum objects in any image, 5).

                Why maximum objects in any image? Because if we have more than one image, each image
                could have different amounts of objects inside and have different dimensions in the
                ground truth (dim 1 of the batch). So we could have the maximum amount of objects
                inside any image and then the rest of the images ground truths could be populated
                with -1.0. So if this loss finds a ground truth box populated with -1.0 it understands
                that it was to match the dimensions and have only one tensor.

        Returns:
            torch.Tensor: The mean CSS loss.
        """
        # We want to use the weights but not backprop over they, we want to backprop over the embeddings
        weights = weights.detach()

        batch_anchors = anchors
        batch_embeddings = embeddings
        batch_annotations = annotations

        losses = []

        for i, anchors in enumerate(batch_anchors):
            embeddings = batch_embeddings[i]
            annotations = batch_annotations[i]

            # Keep only the real labels
            annotations = annotations[annotations[:, -1] != -1]

            # Zero loss for this image if it does not have any annotation
            if annotations.shape[0] == 0:
                losses.append(torch.zeros(1).mean().to(self.device))
                continue

            # Get assignations of the annotations to the anchors
            # Get the assigned annotations (the i-th assigned annotation is the annotation assigned to the i-th
            # anchor)
            # Get the masks to select the anchors assigned to an object (IoU bigger than iou_object threshold)
            assignations = Anchors.assign(anchors, annotations, thresholds=self.iou_thresholds)
            assigned_annotations, selected_anchors_objects, *_ = assignations

            # We must compute the cosine similarity between each embedding and its corresponding weight vector of its
            # assigned annotation. So we can do this by a single matrix multiplication between all the selected anchors
            # as objects embeddings and their corresponding vectors.
            embeddings = embeddings[selected_anchors_objects]  # Shape (selected embeddings, embedding size)
            weights = weights[:, assigned_annotations[selected_anchors_objects, -1].long()]  # Shape (embedding size,)

            loss = torch.matmul(embeddings, weights)  # Shape (selected embeddings,)
            losses.append(loss.mean())

        return torch.stack(losses).mean()
