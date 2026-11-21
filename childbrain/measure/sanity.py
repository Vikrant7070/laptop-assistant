"""A fixed tiny experiment for checking that runs repeat exactly.

A small network learns a made-up rule (no human data: the inputs are random numbers and the
"answer" is a fixed formula). It uses every source of randomness a real run will use: random
starting weights, random data, random batches and dropout.
"""

import torch
from torch import nn

from ..runcard import experiment


@experiment(defaults={"steps": 200, "width": 32, "lr": 0.01, "batch": 64, "dropout": 0.1, "every": 20})
def tiny_train(settings: dict, device: str) -> dict:
    dev = torch.device(device)
    x = torch.randn(1024, 8)
    y = (x[:, :4].sum(dim=1, keepdim=True) - x[:, 4:].prod(dim=1, keepdim=True)).tanh()
    x, y = x.to(dev), y.to(dev)

    w = settings["width"]
    net = nn.Sequential(nn.Linear(8, w), nn.ReLU(), nn.Dropout(settings["dropout"]),
                        nn.Linear(w, w), nn.ReLU(), nn.Linear(w, 1)).to(dev)
    opt = torch.optim.Adam(net.parameters(), lr=settings["lr"])

    losses = []
    for step in range(settings["steps"]):
        idx = torch.randint(0, x.shape[0], (settings["batch"],)).to(dev)
        loss = nn.functional.mse_loss(net(x[idx]), y[idx])
        opt.zero_grad()
        loss.backward()
        opt.step()
        if step % settings["every"] == 0 or step == settings["steps"] - 1:
            losses.append(loss.item())

    net.eval()
    with torch.no_grad():
        final = nn.functional.mse_loss(net(x), y).item()
        weight_sum = sum(p.double().sum().item() for p in net.parameters())
    return {"losses": losses, "final_loss": final, "weight_sum": weight_sum}
