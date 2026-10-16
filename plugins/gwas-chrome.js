// Put back the four things the vendored GWAS plugin leaves out: the y-axis
// scale bar, a legend for the strand colour, a line at y=0, and a threshold
// line at y=-1 on the DR tracks.
//
// WHY THIS EXISTS
//
// The tracks in this browser are drawn by the vendored GWAS plugin's
// LinearManhattanDisplay. Its renderer draws points and gridlines and nothing
// else -- there is not one fillText call in the bundle -- so the only way to
// read a value off a track is the axis, and the axis is not drawn either.
//
// The axis is drawn by core, in WiggleDisplayComponent:
//
//     const { stats, height, graphType } = model
//     ...
//     {stats && graphType ? <svg ...><YScaleBar model={model} /></svg> : null}
//
// and graphType is a getter on the wiggle display model:
//
//     get graphType() {
//       return rendererTypeName === 'XYPlotRenderer' ||
//              rendererTypeName === 'LinePlotRenderer'
//     }
//
// LinearManhattanDisplay overrides rendererTypeName to "LinearManhattanRenderer",
// so graphType is false, so the scale bar is never mounted. Note that the plugin
// also declares `get needsScalebar() { return true }` -- that is dead code in
// JBrowse 2 v4.3.0. `needsScalebar` appears nowhere in the v4.3.0 source; an
// earlier 2.x core used it here and the plugin was written against that. There is
// no config slot for the axis (BaseLinearDisplay offers only
// maxFeatureScreenDensity, fetchSizeLimit, height, mouseover, jexlFilters;
// SharedWiggleConfigSchema only autoscale, minimalTicks, minScore, maxScore,
// numStdDev, scaleType, inverted), so this cannot be switched on from
// config.json.
//
// THE LEGEND
//
// Every GWASTrack in config/tracks.yaml colours its points by insertion strand:
//
//     renderers:
//       LinearManhattanRenderer:
//         color: "jexl:get(feature,'strand')==1?'darkred':'darkblue'"
//
// and nothing on screen says which colour is which. The renderer cannot say it
// either -- its config schema has exactly one slot, `color`, and, again, the
// bundle never draws text. So the key is drawn as HTML over the display, by
// wrapping the display's ReactComponent.
//
// Which colour is which is read off the shipped code, not assumed. The BED
// parser in the app bundle (site/static/js) converts the strand column with
//
//     strand: "+"===d ? 1 : "-"===d ? -1 : "."===d ? 0 : void 0
//
// and the jexl above tests `==1`, so + is darkred and - is darkblue. That
// matters: a legend that is backwards is worse than no legend.
//
// WHY WRAPPING IS SAFE
//
// The plugin registers core's own LinearWiggleDisplayReactComponent for this
// display -- it reaches into the WigglePlugin and takes the component out:
//
//     let { LinearWiggleDisplayReactComponent } = pluginManager
//       .getPlugin('WigglePlugin').exports
//     ... new DisplayType({ name: 'LinearManhattanDisplay', ReactComponent: n })
//
// so the component being wrapped is the one every wiggle track in the app
// renders through, not something the GWAS plugin owns. The wrapper adds one
// positioned div around it and a sibling for the key; it does not resize or
// restyle anything.
//
// THE ZERO LINE
//
// DR, DL and LFC are all signed, and sign is usually what a reader is asking
// about: above zero the insertion is enriched, below it depleted. The renderer
// draws no horizontal reference of any kind -- it has a cross-hatch pass, but it
// only runs when `displayCrossHatches` is set and that is off by default -- so
// the sign boundary is wherever the reader judges y=0 to be from the axis. This
// draws it.
//
// Where y=0 falls is taken from the props the renderer is already handed, not
// recomputed from minScore/maxScore:
//
//     props.scaleOpts.domain   the axis domain, measured as [-12, 4] for an LFC
//                              track configured -12..3 -- the display widens it,
//                              so the config value is NOT the domain
//     props.ticks.range        [y(domain[0]), y(domain[1])], the axis pixel span
//
// Those two give the whole transform for a linear scale, which is the only
// scale these tracks can use: the values are negative, so a log scale is not
// available. Both come in per block, so each block gets its line in its place.
//
// Dashed, not solid. Three reasons, in order of weight:
//
//   * The line is an annotation, and a dashed rule is the convention for one.
//     Solid, it reads as data -- or worse, as the axis.
//   * The LFC cloud is densest where it crosses zero, so a solid line is buried
//     exactly where it is most wanted. Dashes stay legible through it.
//   * The renderer's own gridlines are solid, and they are drawn from
//     ticks.values, which INCLUDES zero. A solid zero line in a different colour
//     would be two lines a pixel apart as soon as the model's `displayCrossHatches`
//     is switched on.
//
// ZERO_DASH / ZERO_COLOR below are the two constants to change if that call is
// ever revisited.
//
// THE THRESHOLD LINE
//
// DR is the depletion rate, and -1 is the cut for a severe phenotype: below it
// the insertion is depleted hard enough to call. That cut is a judgement, not
// arithmetic, and nothing in the track data says where it is -- so it is drawn.
//
// WHICH TRACKS GET ONE. The renderer is shared by all 29 GWASTracks -- DR, DL
// and LFC -- and -1 means something different on each (a log2 fold change of -1
// is a different claim from a depletion rate of -1). So a line at -1 cannot be
// drawn for every track. There is nothing in the renderer's own config to key
// off either: its schema has exactly one slot, `color`, and the display's config
// is not among the props. props.adapterConfig is, though, and it names the file
// being drawn -- every DR track reads data/tracks/<dataset>/DR.bed.gz. Keying on
// that basename also survives track renames, display renames and session
// changes, none of which a displayId would.
//
// ORANGE, NOT RED. The obvious colour for a severity threshold is red, and red
// is the one that does not work: the forward-strand points are already darkred
// (#8b0000), and -1 sits at the bottom of the cloud where those points are
// exactly what is being read, so a red line lands on top of them. Measured in
// CIE76 dE, pure red #d62728 is 23.7 from darkred -- the same colour to the eye.
// Magenta and purple are no better at the other end: dE 50.0 and 34.6 from the
// reverse strand's darkblue. #e08214 clears everything on the track -- 48.1 from
// darkred, 77.3 from the grey zero line, 81.2 from the white background -- and
// orange is the conventional caution/threshold hue, which is the meaning wanted.
//
// LONGER DASHES THAN THE ZERO LINE. Both are annotations, but not the same kind:
// zero is arithmetic and -1 is a chosen cut. Distinguishing them by pattern as
// well as colour carries that difference through a greyscale print or a
// colour-blind reader, and stops the threshold reading as being as absolute as
// zero. THRESHOLD_DASH / THRESHOLD_COLOR below are the knobs.
//
// HOW IT IS ATTACHED
//
// Through the public Core-extendPluggableElement extension point -- the same
// mechanism core plugins use to extend each other, and the same shape core's own
// plugins use for it (see the LinearPileupDisplay and LinearGenomeView
// extensions in the app bundle). Nothing is patched in the vendored bundle: it
// is a byte-for-byte upstream copy, and this file is the only thing standing
// between it and the axis and the key.
//
// Claiming graphType is not the lie it looks like. graphType is consulted in
// exactly three places for a single-source wiggle display: the scale bar
// condition above, the SVG export's copy of the same condition, and the track
// menu, where it gates the "Show inverted" and "Show cross hatches" items. The
// renderer never reads it. The net effect is the axis, plus two menu items that
// work on this display anyway.
//
// Core already does exactly this: SNPCoverageDisplay declares
// `get graphType() { return true }` outright, and it is not a plot of either
// kind. So "graphType means 'this display wants a y-axis'" is how the codebase
// already reads it.
//
// React comes from JBrowseExports["react/jsx-runtime"], the same global the
// vendored bundle itself imports. There is no bare "react" key in the app's
// re-export map, so that is the one to use. jsx/jsxs are called directly rather
// than writing JSX, because this file is hand-written plain JS with no build
// step, served as-is.
//
// The global name and the config.json `plugins` entry are the two halves of the
// JBrowse plugin contract: config says {name: "GWASChrome"}, the browser looks
// for window.JBrowsePluginGWASChrome.

var JBrowsePluginGWASChrome = (function () {
  "use strict";

  var PluginModule = JBrowseExports["@jbrowse/core/Plugin"];
  var Plugin = PluginModule && PluginModule.default ? PluginModule.default : PluginModule;

  var jsxRuntime = JBrowseExports["react/jsx-runtime"];
  var jsx = jsxRuntime.jsx;
  var jsxs = jsxRuntime.jsxs;

  var DISPLAY = "LinearManhattanDisplay";
  var RENDERER = "LinearManhattanRenderer";

  // The zero line. See THE ZERO LINE above for why it is dashed.
  var ZERO_DASH = [4, 3];
  var ZERO_COLOR = "#555";

  // The threshold line. See THE THRESHOLD LINE above for the colour and the
  // longer dash.
  var THRESHOLD_COLOR = "#e08214";
  var THRESHOLD_DASH = [9, 4];

  // Keyed by the basename of the BED the renderer is drawing, so a track only
  // gets a line when the value is meaningful for ITS data. See WHICH TRACKS GET
  // ONE above for why this is not just "always draw -1".
  var THRESHOLDS = {
    "DR.bed.gz": [{ value: -1, color: THRESHOLD_COLOR, dash: THRESHOLD_DASH }],
  };

  // Must match the `color` jexl on every GWASTrack in config/tracks.yaml, and
  // the strand parsing in the app's BED reader. See THE LEGEND above.
  var FORWARD = "darkred";
  var REVERSE = "darkblue";

  var KEY_STYLE = {
    position: "absolute",
    top: "4px",
    right: "6px",
    display: "flex",
    gap: "10px",
    padding: "2px 7px",
    background: "rgba(255, 255, 255, 0.85)",
    border: "1px solid rgba(0, 0, 0, 0.15)",
    borderRadius: "3px",
    font: "11px sans-serif",
    lineHeight: "14px",
    color: "#222",
    // The key sits over the plot area, where the display listens for mousemove
    // to drive the feature tooltip. Without this it would swallow that in the
    // top-right corner of every manhattan track.
    pointerEvents: "none",
    zIndex: 5,
  };

  var ENTRY_STYLE = {
    display: "flex",
    alignItems: "center",
    gap: "4px",
  };

  var DOT_STYLE = {
    width: "9px",
    height: "9px",
    borderRadius: "50%",
    flex: "0 0 auto",
  };

  function entry(color, label, key) {
    return jsxs(
      "span",
      {
        style: ENTRY_STYLE,
        children: [
          jsx("span", { style: Object.assign({ background: color }, DOT_STYLE) }, "dot"),
          jsx("span", { children: label }, "label"),
        ],
      },
      key,
    );
  }

  function strandKey() {
    return jsxs(
      "div",
      {
        style: KEY_STYLE,
        children: [
          entry(FORWARD, "+ forward", "forward"),
          entry(REVERSE, "− reverse", "reverse"),
        ],
      },
      "key",
    );
  }

  // The pixel y for a value on this block's axis, or undefined if this block
  // cannot show it. Both operands come from the props the renderer is already
  // handed; see THE ZERO LINE above for why they are not recomputed from
  // minScore/maxScore.
  function yForValue(props, value) {
    var scaleOpts = props.scaleOpts;
    var ticks = props.ticks;
    var domain = scaleOpts && scaleOpts.domain;
    var range = ticks && ticks.range;
    if (!domain || !range) {
      return undefined;
    }
    // Strictly inside, so a track scaled entirely above or below the value gets
    // no line pinned to its edge claiming to be that value.
    if (!(value > domain[0] && value < domain[1])) {
      return undefined;
    }
    return range[0] + ((value - domain[0]) / (domain[1] - domain[0])) * (range[1] - range[0]);
  }

  function drawHLine(ctx, width, y, color, dash) {
    // A 1px line on an integer coordinate straddles two device pixels and comes
    // out as two half-intensity rows. Half-pixel centres it.
    var py = Math.round(y) + 0.5;
    ctx.save();
    ctx.lineWidth = 1;
    ctx.strokeStyle = color;
    ctx.setLineDash(dash);
    ctx.beginPath();
    ctx.moveTo(0, py);
    ctx.lineTo(width, py);
    ctx.stroke();
    ctx.restore();
  }

  // The basename of the BED this renderer is drawing, e.g. "DR.bed.gz", or
  // undefined if the adapter is not the one this file knows about.
  function bedBasename(props) {
    var location = props.adapterConfig && props.adapterConfig.bedGzLocation;
    var uri = location && location.uri;
    return typeof uri === "string" ? uri.split("/").pop() : undefined;
  }

  function drawReferenceLines(ctx, props) {
    var region = props.regions && props.regions[0];
    if (!region) {
      return;
    }
    // Same span the renderer computes for its own gridlines.
    var width = (region.end - region.start) / props.bpPerPx;

    var zeroY = yForValue(props, 0);
    if (zeroY !== undefined) {
      drawHLine(ctx, width, zeroY, ZERO_COLOR, ZERO_DASH);
    }

    var lines = THRESHOLDS[bedBasename(props)];
    if (!lines) {
      return;
    }
    for (var i = 0; i < lines.length; i++) {
      var y = yForValue(props, lines[i].value);
      if (y !== undefined) {
        drawHLine(ctx, width, y, lines[i].color, lines[i].dash);
      }
    }
  }

  // The wrapper. `position: relative` on the outer div is what the key's
  // absolute position resolves against; no height is set, so the display
  // underneath still decides how tall the track is.
  function withStrandKey(Inner) {
    return function GWASChromeDisplay(props) {
      return jsxs(
        "div",
        {
          style: { position: "relative" },
          children: [jsx(Inner, props, "display"), strandKey()],
        },
        "chrome",
      );
    };
  }

  class GWASChromePlugin extends Plugin {
    constructor() {
      super();
      this.name = "GWASChromePlugin";
      this.version = "1.0.0";
    }

    install(pluginManager) {
      pluginManager.addToExtensionPoint(
        "Core-extendPluggableElement",
        function (element) {
          // Everything else -- adapters, tracks, the other plugins' displays --
          // passes through untouched. Extension point handlers chain, each
          // receiving the previous handler's return value, so returning the
          // element unchanged is what keeps this from swallowing them.
          if (element.name === DISPLAY) {
            // Mutated in place, and the same object returned. It has to be:
            // these are class instances (DisplayType), so copying one with
            // Object.assign yields a plain object with the instance fields and
            // none of the prototype -- and the first prototype method the app
            // reaches for, `configure`, throws "e.configure is not a function"
            // on load. Core's own handlers do it this way too.
            //
            // stateModel is an MST model -- the plugin's stateModelFactory has
            // already been called on it -- so .extend adds a view that shadows
            // the inherited graphType getter.
            element.stateModel = element.stateModel.extend(function () {
              return {
                views: {
                  get graphType() {
                    return true;
                  },
                },
              };
            });

            // Guarded rather than assumed once: addDisplayType takes a factory,
            // so a DisplayType can be built more than once, and a second wrap
            // would draw two keys.
            if (!element.ReactComponent.__gwasStrandKey) {
              var wrapped = withStrandKey(element.ReactComponent);
              wrapped.__gwasStrandKey = true;
              element.ReactComponent = wrapped;
            }
          }

          // The renderer draws the points; the reference lines go on after
          // them, in the same canvas, so they sit under nothing and over the
          // plot.
          //
          // Reached through the instance rather than the class: `draw` lives on
          // the prototype, so assigning here shadows it for this instance only
          // and leaves every other renderer in the app alone. There is no
          // `element.renderer` indirection in JBrowse 4.3.0 to go through --
          // the pluggable element IS the renderer.
          if (element.name === RENDERER && typeof element.draw === "function") {
            if (!element.draw.__gwasZeroLine) {
              var drawWrapped = (function (original) {
                return function (ctx, props) {
                  var result = original.call(this, ctx, props);
                  drawReferenceLines(ctx, props);
                  return result;
                };
              })(element.draw);
              drawWrapped.__gwasZeroLine = true;
              element.draw = drawWrapped;
            }
          }
          return element;
        },
      );
    }
  }

  return { __esModule: true, default: GWASChromePlugin };
})();
