"use strict";

// 小さい CNN の点付け。web_cnn.py と同じ式。重みは強化学習した cnn_small.json。向きは平均しない。

function conv(src, weight, bias, inCh, outCh) {
  const size = 6;
  const out = new Float64Array(outCh * size * size);
  for (let oc = 0; oc < outCh; oc++) {
    const baseW = oc * inCh * 9;
    const biasOc = bias[oc];
    for (let y = 0; y < size; y++) {
      for (let x = 0; x < size; x++) {
        let acc = biasOc;
        for (let ic = 0; ic < inCh; ic++) {
          const srcBase = ic * size * size;
          const wBase = baseW + ic * 9;
          for (let ky = 0; ky < 3; ky++) {
            const iy = y + ky - 1;
            if (iy < 0 || iy >= size) continue;
            const row = srcBase + iy * size;
            const wRow = wBase + ky * 3;
            for (let kx = 0; kx < 3; kx++) {
              const ix = x + kx - 1;
              if (ix >= 0 && ix < size) acc += weight[wRow + kx] * src[row + ix];
            }
          }
        }
        out[(oc * size + y) * size + x] = acc;
      }
    }
  }
  return out;
}

function relu(values) {
  const out = new Float64Array(values.length);
  for (let index = 0; index < values.length; index++) out[index] = values[index] > 0 ? values[index] : 0;
  return out;
}

function residual(src, block, channels) {
  const hidden = relu(conv(src, block.conv1_w, block.conv1_b, channels, channels));
  const added = conv(hidden, block.conv2_w, block.conv2_b, channels, channels);
  const out = new Float64Array(src.length);
  for (let index = 0; index < src.length; index++) {
    const sum = src[index] + added[index];
    out[index] = sum > 0 ? sum : 0;
  }
  return out;
}

function pool(hidden, channels) {
  const pooled = new Float64Array(channels * 2);
  for (let channel = 0; channel < channels; channel++) {
    const start = channel * 36;
    let total = 0;
    let peak = hidden[start];
    for (let cell = 0; cell < 36; cell++) {
      const value = hidden[start + cell];
      total += value;
      if (value > peak) peak = value;
    }
    pooled[channel] = total / 36;
    pooled[channels + channel] = peak;
  }
  return pooled;
}

function linear(src, weight, bias, outCh) {
  const inCh = src.length;
  const out = new Float64Array(outCh);
  for (let oc = 0; oc < outCh; oc++) {
    let acc = bias[oc];
    const base = oc * inCh;
    for (let ic = 0; ic < inCh; ic++) acc += weight[base + ic] * src[ic];
    out[oc] = acc;
  }
  return out;
}

function forwardOne(planes, lines, weights) {
  const channels = weights.channels;
  let hidden = relu(conv(planes, weights.stem_w, weights.stem_b, 10, channels));
  for (const block of weights.tower) hidden = residual(hidden, block, channels);
  const pooled = new Float64Array(channels * 2 + lines.length);
  const summary = pool(hidden, channels);
  pooled.set(summary);
  for (let index = 0; index < lines.length; index++) pooled[summary.length + index] = lines[index];
  const hiddenFc = relu(linear(pooled, weights.fc1_w, weights.fc1_b, 64));
  return linear(hiddenFc, weights.fc2_w, weights.fc2_b, 1)[0];
}

function scoreBatch(planes, lines, count, weights) {
  const flat = planes instanceof Float64Array ? planes : Float64Array.from(planes);
  const span = 10 * 6 * 6;
  const scores = new Array(count);
  for (let index = 0; index < count; index++) {
    scores[index] = forwardOne(flat.subarray(index * span, (index + 1) * span), lines, weights);
  }
  return scores;
}

const api = { scoreBatch, forwardOne };
if (typeof module === "object" && module.exports) module.exports = api;
else self.TarutaruCnn = api;
