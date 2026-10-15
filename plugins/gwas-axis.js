// Put the y-axis scale bar back on the GWAS manhattan tracks.
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
// WHAT THIS DOES
//
// Overrides that one getter to true on the registered display, through the
// public Core-extendPluggableElement extension point -- the same mechanism core
// plugins use to extend each other, and the same shape core's own plugins use
// for it (see the LinearPileupDisplay and LinearGenomeView extensions in the
// app bundle). Nothing is patched in the vendored bundle: it is a byte-for-byte
// upstream copy, and this file is the only thing standing between it and the
// axis.
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
// The plugin is hand-written plain JS with no build step, served as-is. It reads
// core out of the JBrowseExports global that every UMD plugin in the app uses.
// The global name and the config.json `plugins` entry are the two halves of the
// JBrowse plugin contract: config says {name: "GWASAxis"}, the browser looks for
// window.JBrowsePluginGWASAxis.

var JBrowsePluginGWASAxis = (function () {
  "use strict";

  var PluginModule = JBrowseExports["@jbrowse/core/Plugin"];
  var Plugin = PluginModule && PluginModule.default ? PluginModule.default : PluginModule;

  var DISPLAY = "LinearManhattanDisplay";

  // Must actually extend Plugin. The app calls configure() on every loaded
  // plugin at startup, and that is inherited from the base class -- a plugin
  // class that merely looks like one fails the whole app with
  // "e.configure is not a function" before the view ever renders.
  class GWASAxisPlugin extends Plugin {
    constructor() {
      super();
      this.name = "GWASAxisPlugin";
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
          }
          return element;
        },
      );
    }
  }

  return { __esModule: true, default: GWASAxisPlugin };
})();
