"""Extract stats from the PrintLogger."""
import click

from torchsight.loggers import PrintLogger


@click.command()
@click.argument('log_file')
@click.option('-k', '--keys', default='Loss Class. Regr. Simil. w-norm LR', show_default=True)
@click.option('-ek', '--epoch-key', default='Epoch', help='The key in the logs that indicates the epoch.')
@click.option('--just', default=12, show_default=True)
def printlogger(log_file, keys, epoch_key, just):
    """Get the mean loss per epoch over the training dataset and validation dataset.

    This stats could be generated from the logs that generated the PrintLogger and were
    saved in a file.

    For example:

    $ python cli.py train dldenet ~/dataset/coco --logs-dir logs/ > logs/logs.txt

    This command indicates that the trainer save the checkpoints to logs/ and the logger
    save the description into logs/description.txt; and with the '>' we put all the prints
    into the logs/logs.txt file.

    Now you can use:

    $ python cli.py stats printlogger logs/logs.txt

    And you'll get something like:

    EPOCH |      TRAIN LOSS | VALIDATION LOSS

        1 |       1.9204515 |       1.1482288

        2 |       1.1309012 | ---

    Where the `---` means that there is no data yet to show.
    """
    logger = PrintLogger()
    keys = keys.split()

    def accumulate(logs):
        epochs = {}

        for log in logs:
            epoch = log[epoch_key]

            if epoch not in epochs:
                epochs[epoch] = {}

            for k in keys:
                if k not in epochs[epoch]:
                    epochs[epoch][k] = {'sum': 0, 'count': 0}

                value = log.get(k, None)
                epochs[epoch][k]['sum'] += float(value) if value is not None else 0
                epochs[epoch][k]['count'] += 1 if value is not None else 0

        return epochs

    train = accumulate(logger.read(log_file, keep=lambda x: x[:10] == '[Training]'))
    valid = accumulate(logger.read(log_file, keep=lambda x: x[:12] == '[Validating]'))

    headers = ['Epoch']
    for key in keys:
        headers += [key.center(just * 2 + 1)]

    print(' | '.join(headers))

    for epoch in train:
        values = [str(epoch).center(5)]

        for k in keys:
            if train[epoch][k]['count'] == 0:
                train_value = '---'.rjust(just)
            else:
                train_value = '{:.7f}'.format(train[epoch][k]['sum'] / train[epoch][k]['count']).rjust(just)

            if valid.get(epoch, None) is None or valid[epoch].get(k, None) is None or valid[epoch][k]['count'] == 0:
                valid_value = '---'.rjust(just)
            else:
                valid_value = '{:.7f}'.format(valid[epoch][k]['sum'] / valid[epoch][k]['count']).rjust(just)

            values.append('{} {}'.format(train_value, valid_value))

        print(' | '.join(values))
