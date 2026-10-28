import { createRequire } from 'node:module'; const require = createRequire(import.meta.url);
var __create = Object.create;
var __defProp = Object.defineProperty;
var __getOwnPropDesc = Object.getOwnPropertyDescriptor;
var __getOwnPropNames = Object.getOwnPropertyNames;
var __getProtoOf = Object.getPrototypeOf;
var __hasOwnProp = Object.prototype.hasOwnProperty;
var __require = /* @__PURE__ */ ((x) => typeof require !== "undefined" ? require : typeof Proxy !== "undefined" ? new Proxy(x, {
  get: (a, b) => (typeof require !== "undefined" ? require : a)[b]
}) : x)(function(x) {
  if (typeof require !== "undefined") return require.apply(this, arguments);
  throw Error('Dynamic require of "' + x + '" is not supported');
});
var __commonJS = (cb, mod) => function __require2() {
  try {
    return mod || (0, cb[__getOwnPropNames(cb)[0]])((mod = { exports: {} }).exports, mod), mod.exports;
  } catch (e) {
    throw mod = 0, e;
  }
};
var __copyProps = (to, from, except, desc) => {
  if (from && typeof from === "object" || typeof from === "function") {
    for (let key of __getOwnPropNames(from))
      if (!__hasOwnProp.call(to, key) && key !== except)
        __defProp(to, key, { get: () => from[key], enumerable: !(desc = __getOwnPropDesc(from, key)) || desc.enumerable });
  }
  return to;
};
var __toESM = (mod, isNodeMode, target) => (target = mod != null ? __create(__getProtoOf(mod)) : {}, __copyProps(
  // If the importer is in node compatibility mode or this is not an ESM
  // file that has been converted to a CommonJS file using a Babel-
  // compatible transform (i.e. "__esModule" has not been set), then set
  // "default" to the CommonJS "module.exports" for node compatibility.
  isNodeMode || !mod || !mod.__esModule ? __defProp(target, "default", { value: mod, enumerable: true }) : target,
  mod
));

// ../../node_modules/ws/lib/constants.js
var require_constants = __commonJS({
  "../../node_modules/ws/lib/constants.js"(exports, module) {
    "use strict";
    var BINARY_TYPES = ["nodebuffer", "arraybuffer", "fragments"];
    var hasBlob = typeof Blob !== "undefined";
    if (hasBlob) BINARY_TYPES.push("blob");
    module.exports = {
      BINARY_TYPES,
      CLOSE_TIMEOUT: 3e4,
      EMPTY_BUFFER: Buffer.alloc(0),
      GUID: "258EAFA5-E914-47DA-95CA-C5AB0DC85B11",
      hasBlob,
      kForOnEventAttribute: /* @__PURE__ */ Symbol("kIsForOnEventAttribute"),
      kListener: /* @__PURE__ */ Symbol("kListener"),
      kStatusCode: /* @__PURE__ */ Symbol("status-code"),
      kWebSocket: /* @__PURE__ */ Symbol("websocket"),
      NOOP: () => {
      }
    };
  }
});

// ../../node_modules/ws/lib/buffer-util.js
var require_buffer_util = __commonJS({
  "../../node_modules/ws/lib/buffer-util.js"(exports, module) {
    "use strict";
    var { EMPTY_BUFFER } = require_constants();
    var FastBuffer = Buffer[Symbol.species];
    function concat(list2, totalLength) {
      if (list2.length === 0) return EMPTY_BUFFER;
      if (list2.length === 1) return list2[0];
      const target = Buffer.allocUnsafe(totalLength);
      let offset = 0;
      for (let i = 0; i < list2.length; i++) {
        const buf = list2[i];
        target.set(buf, offset);
        offset += buf.length;
      }
      if (offset < totalLength) {
        return new FastBuffer(target.buffer, target.byteOffset, offset);
      }
      return target;
    }
    function _mask(source, mask, output, offset, length) {
      for (let i = 0; i < length; i++) {
        output[offset + i] = source[i] ^ mask[i & 3];
      }
    }
    function _unmask(buffer, mask) {
      for (let i = 0; i < buffer.length; i++) {
        buffer[i] ^= mask[i & 3];
      }
    }
    function toArrayBuffer(buf) {
      if (buf.length === buf.buffer.byteLength) {
        return buf.buffer;
      }
      return buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.length);
    }
    function toBuffer(data2) {
      toBuffer.readOnly = true;
      if (Buffer.isBuffer(data2)) return data2;
      let buf;
      if (data2 instanceof ArrayBuffer) {
        buf = new FastBuffer(data2);
      } else if (ArrayBuffer.isView(data2)) {
        buf = new FastBuffer(data2.buffer, data2.byteOffset, data2.byteLength);
      } else {
        buf = Buffer.from(data2);
        toBuffer.readOnly = false;
      }
      return buf;
    }
    module.exports = {
      concat,
      mask: _mask,
      toArrayBuffer,
      toBuffer,
      unmask: _unmask
    };
    if (!process.env.WS_NO_BUFFER_UTIL) {
      try {
        const bufferUtil = __require("bufferutil");
        module.exports.mask = function(source, mask, output, offset, length) {
          if (length < 48) _mask(source, mask, output, offset, length);
          else bufferUtil.mask(source, mask, output, offset, length);
        };
        module.exports.unmask = function(buffer, mask) {
          if (buffer.length < 32) _unmask(buffer, mask);
          else bufferUtil.unmask(buffer, mask);
        };
      } catch (e) {
      }
    }
  }
});

// ../../node_modules/ws/lib/limiter.js
var require_limiter = __commonJS({
  "../../node_modules/ws/lib/limiter.js"(exports, module) {
    "use strict";
    var kDone = /* @__PURE__ */ Symbol("kDone");
    var kRun = /* @__PURE__ */ Symbol("kRun");
    var Limiter = class {
      /**
       * Creates a new `Limiter`.
       *
       * @param {Number} [concurrency=Infinity] The maximum number of jobs allowed
       *     to run concurrently
       */
      constructor(concurrency) {
        this[kDone] = () => {
          this.pending--;
          this[kRun]();
        };
        this.concurrency = concurrency || Infinity;
        this.jobs = [];
        this.pending = 0;
      }
      /**
       * Adds a job to the queue.
       *
       * @param {Function} job The job to run
       * @public
       */
      add(job) {
        this.jobs.push(job);
        this[kRun]();
      }
      /**
       * Removes a job from the queue and runs it if possible.
       *
       * @private
       */
      [kRun]() {
        if (this.pending === this.concurrency) return;
        if (this.jobs.length) {
          const job = this.jobs.shift();
          this.pending++;
          job(this[kDone]);
        }
      }
    };
    module.exports = Limiter;
  }
});

// ../../node_modules/ws/lib/permessage-deflate.js
var require_permessage_deflate = __commonJS({
  "../../node_modules/ws/lib/permessage-deflate.js"(exports, module) {
    "use strict";
    var zlib = __require("zlib");
    var bufferUtil = require_buffer_util();
    var Limiter = require_limiter();
    var { kStatusCode } = require_constants();
    var FastBuffer = Buffer[Symbol.species];
    var TRAILER = Buffer.from([0, 0, 255, 255]);
    var kPerMessageDeflate = /* @__PURE__ */ Symbol("permessage-deflate");
    var kTotalLength = /* @__PURE__ */ Symbol("total-length");
    var kCallback = /* @__PURE__ */ Symbol("callback");
    var kBuffers = /* @__PURE__ */ Symbol("buffers");
    var kError = /* @__PURE__ */ Symbol("error");
    var zlibLimiter;
    var PerMessageDeflate2 = class {
      /**
       * Creates a PerMessageDeflate instance.
       *
       * @param {Object} [options] Configuration options
       * @param {(Boolean|Number)} [options.clientMaxWindowBits] Advertise support
       *     for, or request, a custom client window size
       * @param {Boolean} [options.clientNoContextTakeover=false] Advertise/
       *     acknowledge disabling of client context takeover
       * @param {Number} [options.concurrencyLimit=10] The number of concurrent
       *     calls to zlib
       * @param {Boolean} [options.isServer=false] Create the instance in either
       *     server or client mode
       * @param {Number} [options.maxPayload=0] The maximum allowed message length
       * @param {(Boolean|Number)} [options.serverMaxWindowBits] Request/confirm the
       *     use of a custom server window size
       * @param {Boolean} [options.serverNoContextTakeover=false] Request/accept
       *     disabling of server context takeover
       * @param {Number} [options.threshold=1024] Size (in bytes) below which
       *     messages should not be compressed if context takeover is disabled
       * @param {Object} [options.zlibDeflateOptions] Options to pass to zlib on
       *     deflate
       * @param {Object} [options.zlibInflateOptions] Options to pass to zlib on
       *     inflate
       */
      constructor(options) {
        this._options = options || {};
        this._threshold = this._options.threshold !== void 0 ? this._options.threshold : 1024;
        this._maxPayload = this._options.maxPayload | 0;
        this._isServer = !!this._options.isServer;
        this._deflate = null;
        this._inflate = null;
        this.params = null;
        if (!zlibLimiter) {
          const concurrency = this._options.concurrencyLimit !== void 0 ? this._options.concurrencyLimit : 10;
          zlibLimiter = new Limiter(concurrency);
        }
      }
      /**
       * @type {String}
       */
      static get extensionName() {
        return "permessage-deflate";
      }
      /**
       * Create an extension negotiation offer.
       *
       * @return {Object} Extension parameters
       * @public
       */
      offer() {
        const params = {};
        if (this._options.serverNoContextTakeover) {
          params.server_no_context_takeover = true;
        }
        if (this._options.clientNoContextTakeover) {
          params.client_no_context_takeover = true;
        }
        if (this._options.serverMaxWindowBits) {
          params.server_max_window_bits = this._options.serverMaxWindowBits;
        }
        if (this._options.clientMaxWindowBits) {
          params.client_max_window_bits = this._options.clientMaxWindowBits;
        } else if (this._options.clientMaxWindowBits == null) {
          params.client_max_window_bits = true;
        }
        return params;
      }
      /**
       * Accept an extension negotiation offer/response.
       *
       * @param {Array} configurations The extension negotiation offers/reponse
       * @return {Object} Accepted configuration
       * @public
       */
      accept(configurations) {
        configurations = this.normalizeParams(configurations);
        this.params = this._isServer ? this.acceptAsServer(configurations) : this.acceptAsClient(configurations);
        return this.params;
      }
      /**
       * Releases all resources used by the extension.
       *
       * @public
       */
      cleanup() {
        if (this._inflate) {
          this._inflate.close();
          this._inflate = null;
        }
        if (this._deflate) {
          const callback = this._deflate[kCallback];
          this._deflate.close();
          this._deflate = null;
          if (callback) {
            callback(
              new Error(
                "The deflate stream was closed while data was being processed"
              )
            );
          }
        }
      }
      /**
       *  Accept an extension negotiation offer.
       *
       * @param {Array} offers The extension negotiation offers
       * @return {Object} Accepted configuration
       * @private
       */
      acceptAsServer(offers) {
        const opts = this._options;
        const accepted = offers.find((params) => {
          if (opts.serverNoContextTakeover === false && params.server_no_context_takeover || params.server_max_window_bits && (opts.serverMaxWindowBits === false || typeof opts.serverMaxWindowBits === "number" && opts.serverMaxWindowBits > params.server_max_window_bits) || typeof opts.clientMaxWindowBits === "number" && (typeof params.client_max_window_bits === "number" ? opts.clientMaxWindowBits > params.client_max_window_bits : !params.client_max_window_bits)) {
            return false;
          }
          return true;
        });
        if (!accepted) {
          throw new Error("None of the extension offers can be accepted");
        }
        if (opts.serverNoContextTakeover) {
          accepted.server_no_context_takeover = true;
        }
        if (opts.clientNoContextTakeover) {
          accepted.client_no_context_takeover = true;
        }
        if (typeof opts.serverMaxWindowBits === "number") {
          accepted.server_max_window_bits = opts.serverMaxWindowBits;
        }
        if (typeof opts.clientMaxWindowBits === "number") {
          accepted.client_max_window_bits = opts.clientMaxWindowBits;
        } else if (accepted.client_max_window_bits === true || opts.clientMaxWindowBits === false) {
          delete accepted.client_max_window_bits;
        }
        return accepted;
      }
      /**
       * Accept the extension negotiation response.
       *
       * @param {Array} response The extension negotiation response
       * @return {Object} Accepted configuration
       * @private
       */
      acceptAsClient(response) {
        const params = response[0];
        if (this._options.clientNoContextTakeover === false && params.client_no_context_takeover) {
          throw new Error('Unexpected parameter "client_no_context_takeover"');
        }
        if (!params.client_max_window_bits) {
          if (typeof this._options.clientMaxWindowBits === "number") {
            params.client_max_window_bits = this._options.clientMaxWindowBits;
          }
        } else if (this._options.clientMaxWindowBits === false || typeof this._options.clientMaxWindowBits === "number" && params.client_max_window_bits > this._options.clientMaxWindowBits) {
          throw new Error(
            'Unexpected or invalid parameter "client_max_window_bits"'
          );
        }
        return params;
      }
      /**
       * Normalize parameters.
       *
       * @param {Array} configurations The extension negotiation offers/reponse
       * @return {Array} The offers/response with normalized parameters
       * @private
       */
      normalizeParams(configurations) {
        configurations.forEach((params) => {
          Object.keys(params).forEach((key) => {
            let value = params[key];
            if (value.length > 1) {
              throw new Error(`Parameter "${key}" must have only a single value`);
            }
            value = value[0];
            if (key === "client_max_window_bits") {
              if (value !== true) {
                const num = +value;
                if (!Number.isInteger(num) || num < 8 || num > 15) {
                  throw new TypeError(
                    `Invalid value for parameter "${key}": ${value}`
                  );
                }
                value = num;
              } else if (!this._isServer) {
                throw new TypeError(
                  `Invalid value for parameter "${key}": ${value}`
                );
              }
            } else if (key === "server_max_window_bits") {
              const num = +value;
              if (!Number.isInteger(num) || num < 8 || num > 15) {
                throw new TypeError(
                  `Invalid value for parameter "${key}": ${value}`
                );
              }
              value = num;
            } else if (key === "client_no_context_takeover" || key === "server_no_context_takeover") {
              if (value !== true) {
                throw new TypeError(
                  `Invalid value for parameter "${key}": ${value}`
                );
              }
            } else {
              throw new Error(`Unknown parameter "${key}"`);
            }
            params[key] = value;
          });
        });
        return configurations;
      }
      /**
       * Decompress data. Concurrency limited.
       *
       * @param {Buffer} data Compressed data
       * @param {Boolean} fin Specifies whether or not this is the last fragment
       * @param {Function} callback Callback
       * @public
       */
      decompress(data2, fin, callback) {
        zlibLimiter.add((done) => {
          this._decompress(data2, fin, (err, result) => {
            done();
            callback(err, result);
          });
        });
      }
      /**
       * Compress data. Concurrency limited.
       *
       * @param {(Buffer|String)} data Data to compress
       * @param {Boolean} fin Specifies whether or not this is the last fragment
       * @param {Function} callback Callback
       * @public
       */
      compress(data2, fin, callback) {
        zlibLimiter.add((done) => {
          this._compress(data2, fin, (err, result) => {
            done();
            callback(err, result);
          });
        });
      }
      /**
       * Decompress data.
       *
       * @param {Buffer} data Compressed data
       * @param {Boolean} fin Specifies whether or not this is the last fragment
       * @param {Function} callback Callback
       * @private
       */
      _decompress(data2, fin, callback) {
        const endpoint = this._isServer ? "client" : "server";
        if (!this._inflate) {
          const key = `${endpoint}_max_window_bits`;
          const windowBits = typeof this.params[key] !== "number" ? zlib.Z_DEFAULT_WINDOWBITS : this.params[key];
          this._inflate = zlib.createInflateRaw({
            ...this._options.zlibInflateOptions,
            windowBits
          });
          this._inflate[kPerMessageDeflate] = this;
          this._inflate[kTotalLength] = 0;
          this._inflate[kBuffers] = [];
          this._inflate.on("error", inflateOnError);
          this._inflate.on("data", inflateOnData);
        }
        this._inflate[kCallback] = callback;
        this._inflate.write(data2);
        if (fin) this._inflate.write(TRAILER);
        this._inflate.flush(() => {
          const err = this._inflate[kError];
          if (err) {
            this._inflate.close();
            this._inflate = null;
            callback(err);
            return;
          }
          const data3 = bufferUtil.concat(
            this._inflate[kBuffers],
            this._inflate[kTotalLength]
          );
          if (this._inflate._readableState.endEmitted) {
            this._inflate.close();
            this._inflate = null;
          } else {
            this._inflate[kTotalLength] = 0;
            this._inflate[kBuffers] = [];
            if (fin && this.params[`${endpoint}_no_context_takeover`]) {
              this._inflate.reset();
            }
          }
          callback(null, data3);
        });
      }
      /**
       * Compress data.
       *
       * @param {(Buffer|String)} data Data to compress
       * @param {Boolean} fin Specifies whether or not this is the last fragment
       * @param {Function} callback Callback
       * @private
       */
      _compress(data2, fin, callback) {
        const endpoint = this._isServer ? "server" : "client";
        if (!this._deflate) {
          const key = `${endpoint}_max_window_bits`;
          const windowBits = typeof this.params[key] !== "number" ? zlib.Z_DEFAULT_WINDOWBITS : this.params[key];
          this._deflate = zlib.createDeflateRaw({
            ...this._options.zlibDeflateOptions,
            windowBits
          });
          this._deflate[kTotalLength] = 0;
          this._deflate[kBuffers] = [];
          this._deflate.on("data", deflateOnData);
        }
        this._deflate[kCallback] = callback;
        this._deflate.write(data2);
        this._deflate.flush(zlib.Z_SYNC_FLUSH, () => {
          if (!this._deflate) {
            return;
          }
          let data3 = bufferUtil.concat(
            this._deflate[kBuffers],
            this._deflate[kTotalLength]
          );
          if (fin) {
            data3 = new FastBuffer(data3.buffer, data3.byteOffset, data3.length - 4);
          }
          this._deflate[kCallback] = null;
          this._deflate[kTotalLength] = 0;
          this._deflate[kBuffers] = [];
          if (fin && this.params[`${endpoint}_no_context_takeover`]) {
            this._deflate.reset();
          }
          callback(null, data3);
        });
      }
    };
    module.exports = PerMessageDeflate2;
    function deflateOnData(chunk) {
      this[kBuffers].push(chunk);
      this[kTotalLength] += chunk.length;
    }
    function inflateOnData(chunk) {
      this[kTotalLength] += chunk.length;
      if (this[kPerMessageDeflate]._maxPayload < 1 || this[kTotalLength] <= this[kPerMessageDeflate]._maxPayload) {
        this[kBuffers].push(chunk);
        return;
      }
      this[kError] = new RangeError("Max payload size exceeded");
      this[kError].code = "WS_ERR_UNSUPPORTED_MESSAGE_LENGTH";
      this[kError][kStatusCode] = 1009;
      this.removeListener("data", inflateOnData);
      this.reset();
    }
    function inflateOnError(err) {
      this[kPerMessageDeflate]._inflate = null;
      if (this[kError]) {
        this[kCallback](this[kError]);
        return;
      }
      err[kStatusCode] = 1007;
      this[kCallback](err);
    }
  }
});

// ../../node_modules/ws/lib/validation.js
var require_validation = __commonJS({
  "../../node_modules/ws/lib/validation.js"(exports, module) {
    "use strict";
    var { isUtf8 } = __require("buffer");
    var { hasBlob } = require_constants();
    var tokenChars = [
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      // 0 - 15
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
      // 16 - 31
      0,
      1,
      0,
      1,
      1,
      1,
      1,
      1,
      0,
      0,
      1,
      1,
      0,
      1,
      1,
      0,
      // 32 - 47
      1,
      1,
      1,
      1,
      1,
      1,
      1,
      1,
      1,
      1,
      0,
      0,
      0,
      0,
      0,
      0,
      // 48 - 63
      0,
      1,
      1,
      1,
      1,
      1,
      1,
      1,
      1,
      1,
      1,
      1,
      1,
      1,
      1,
      1,
      // 64 - 79
      1,
      1,
      1,
      1,
      1,
      1,
      1,
      1,
      1,
      1,
      1,
      0,
      0,
      0,
      1,
      1,
      // 80 - 95
      1,
      1,
      1,
      1,
      1,
      1,
      1,
      1,
      1,
      1,
      1,
      1,
      1,
      1,
      1,
      1,
      // 96 - 111
      1,
      1,
      1,
      1,
      1,
      1,
      1,
      1,
      1,
      1,
      1,
      0,
      1,
      0,
      1,
      0
      // 112 - 127
    ];
    function isValidStatusCode(code) {
      return code >= 1e3 && code <= 1014 && code !== 1004 && code !== 1005 && code !== 1006 || code >= 3e3 && code <= 4999;
    }
    function _isValidUTF8(buf) {
      const len = buf.length;
      let i = 0;
      while (i < len) {
        if ((buf[i] & 128) === 0) {
          i++;
        } else if ((buf[i] & 224) === 192) {
          if (i + 1 === len || (buf[i + 1] & 192) !== 128 || (buf[i] & 254) === 192) {
            return false;
          }
          i += 2;
        } else if ((buf[i] & 240) === 224) {
          if (i + 2 >= len || (buf[i + 1] & 192) !== 128 || (buf[i + 2] & 192) !== 128 || buf[i] === 224 && (buf[i + 1] & 224) === 128 || // Overlong
          buf[i] === 237 && (buf[i + 1] & 224) === 160) {
            return false;
          }
          i += 3;
        } else if ((buf[i] & 248) === 240) {
          if (i + 3 >= len || (buf[i + 1] & 192) !== 128 || (buf[i + 2] & 192) !== 128 || (buf[i + 3] & 192) !== 128 || buf[i] === 240 && (buf[i + 1] & 240) === 128 || // Overlong
          buf[i] === 244 && buf[i + 1] > 143 || buf[i] > 244) {
            return false;
          }
          i += 4;
        } else {
          return false;
        }
      }
      return true;
    }
    function isBlob(value) {
      return hasBlob && typeof value === "object" && typeof value.arrayBuffer === "function" && typeof value.type === "string" && typeof value.stream === "function" && (value[Symbol.toStringTag] === "Blob" || value[Symbol.toStringTag] === "File");
    }
    module.exports = {
      isBlob,
      isValidStatusCode,
      isValidUTF8: _isValidUTF8,
      tokenChars
    };
    if (isUtf8) {
      module.exports.isValidUTF8 = function(buf) {
        return buf.length < 24 ? _isValidUTF8(buf) : isUtf8(buf);
      };
    } else if (!process.env.WS_NO_UTF_8_VALIDATE) {
      try {
        const isValidUTF8 = __require("utf-8-validate");
        module.exports.isValidUTF8 = function(buf) {
          return buf.length < 32 ? _isValidUTF8(buf) : isValidUTF8(buf);
        };
      } catch (e) {
      }
    }
  }
});

// ../../node_modules/ws/lib/receiver.js
var require_receiver = __commonJS({
  "../../node_modules/ws/lib/receiver.js"(exports, module) {
    "use strict";
    var { Writable } = __require("stream");
    var PerMessageDeflate2 = require_permessage_deflate();
    var {
      BINARY_TYPES,
      EMPTY_BUFFER,
      kStatusCode,
      kWebSocket
    } = require_constants();
    var { concat, toArrayBuffer, unmask } = require_buffer_util();
    var { isValidStatusCode, isValidUTF8 } = require_validation();
    var FastBuffer = Buffer[Symbol.species];
    var GET_INFO = 0;
    var GET_PAYLOAD_LENGTH_16 = 1;
    var GET_PAYLOAD_LENGTH_64 = 2;
    var GET_MASK = 3;
    var GET_DATA = 4;
    var INFLATING = 5;
    var DEFER_EVENT = 6;
    var Receiver2 = class extends Writable {
      /**
       * Creates a Receiver instance.
       *
       * @param {Object} [options] Options object
       * @param {Boolean} [options.allowSynchronousEvents=true] Specifies whether
       *     any of the `'message'`, `'ping'`, and `'pong'` events can be emitted
       *     multiple times in the same tick
       * @param {String} [options.binaryType=nodebuffer] The type for binary data
       * @param {Object} [options.extensions] An object containing the negotiated
       *     extensions
       * @param {Boolean} [options.isServer=false] Specifies whether to operate in
       *     client or server mode
       * @param {Number} [options.maxBufferedChunks=0] The maximum number of
       *     buffered data chunks
       * @param {Number} [options.maxFragments=0] The maximum number of message
       *     fragments
       * @param {Number} [options.maxPayload=0] The maximum allowed message length
       * @param {Boolean} [options.skipUTF8Validation=false] Specifies whether or
       *     not to skip UTF-8 validation for text and close messages
       */
      constructor(options = {}) {
        super();
        this._allowSynchronousEvents = options.allowSynchronousEvents !== void 0 ? options.allowSynchronousEvents : true;
        this._binaryType = options.binaryType || BINARY_TYPES[0];
        this._extensions = options.extensions || {};
        this._isServer = !!options.isServer;
        this._maxBufferedChunks = options.maxBufferedChunks | 0;
        this._maxFragments = options.maxFragments | 0;
        this._maxPayload = options.maxPayload | 0;
        this._skipUTF8Validation = !!options.skipUTF8Validation;
        this[kWebSocket] = void 0;
        this._bufferedBytes = 0;
        this._buffers = [];
        this._compressed = false;
        this._payloadLength = 0;
        this._mask = void 0;
        this._fragmented = 0;
        this._masked = false;
        this._fin = false;
        this._opcode = 0;
        this._totalPayloadLength = 0;
        this._messageLength = 0;
        this._numFragments = 0;
        this._fragments = [];
        this._errored = false;
        this._loop = false;
        this._state = GET_INFO;
      }
      /**
       * Implements `Writable.prototype._write()`.
       *
       * @param {Buffer} chunk The chunk of data to write
       * @param {String} encoding The character encoding of `chunk`
       * @param {Function} cb Callback
       * @private
       */
      _write(chunk, encoding, cb) {
        if (this._opcode === 8 && this._state == GET_INFO) return cb();
        if (this._maxBufferedChunks > 0 && this._buffers.length >= this._maxBufferedChunks) {
          cb(
            this.createError(
              RangeError,
              "Too many buffered chunks",
              false,
              1008,
              "WS_ERR_TOO_MANY_BUFFERED_PARTS"
            )
          );
          return;
        }
        this._bufferedBytes += chunk.length;
        this._buffers.push(chunk);
        this.startLoop(cb);
      }
      /**
       * Consumes `n` bytes from the buffered data.
       *
       * @param {Number} n The number of bytes to consume
       * @return {Buffer} The consumed bytes
       * @private
       */
      consume(n) {
        this._bufferedBytes -= n;
        if (n === this._buffers[0].length) return this._buffers.shift();
        if (n < this._buffers[0].length) {
          const buf = this._buffers[0];
          this._buffers[0] = new FastBuffer(
            buf.buffer,
            buf.byteOffset + n,
            buf.length - n
          );
          return new FastBuffer(buf.buffer, buf.byteOffset, n);
        }
        const dst = Buffer.allocUnsafe(n);
        do {
          const buf = this._buffers[0];
          const offset = dst.length - n;
          if (n >= buf.length) {
            dst.set(this._buffers.shift(), offset);
          } else {
            dst.set(new Uint8Array(buf.buffer, buf.byteOffset, n), offset);
            this._buffers[0] = new FastBuffer(
              buf.buffer,
              buf.byteOffset + n,
              buf.length - n
            );
          }
          n -= buf.length;
        } while (n > 0);
        return dst;
      }
      /**
       * Starts the parsing loop.
       *
       * @param {Function} cb Callback
       * @private
       */
      startLoop(cb) {
        this._loop = true;
        do {
          switch (this._state) {
            case GET_INFO:
              this.getInfo(cb);
              break;
            case GET_PAYLOAD_LENGTH_16:
              this.getPayloadLength16(cb);
              break;
            case GET_PAYLOAD_LENGTH_64:
              this.getPayloadLength64(cb);
              break;
            case GET_MASK:
              this.getMask();
              break;
            case GET_DATA:
              this.getData(cb);
              break;
            case INFLATING:
            case DEFER_EVENT:
              this._loop = false;
              return;
          }
        } while (this._loop);
        if (!this._errored) cb();
      }
      /**
       * Reads the first two bytes of a frame.
       *
       * @param {Function} cb Callback
       * @private
       */
      getInfo(cb) {
        if (this._bufferedBytes < 2) {
          this._loop = false;
          return;
        }
        const buf = this.consume(2);
        if ((buf[0] & 48) !== 0) {
          const error = this.createError(
            RangeError,
            "RSV2 and RSV3 must be clear",
            true,
            1002,
            "WS_ERR_UNEXPECTED_RSV_2_3"
          );
          cb(error);
          return;
        }
        const compressed = (buf[0] & 64) === 64;
        if (compressed && !this._extensions[PerMessageDeflate2.extensionName]) {
          const error = this.createError(
            RangeError,
            "RSV1 must be clear",
            true,
            1002,
            "WS_ERR_UNEXPECTED_RSV_1"
          );
          cb(error);
          return;
        }
        this._fin = (buf[0] & 128) === 128;
        this._opcode = buf[0] & 15;
        this._payloadLength = buf[1] & 127;
        if (this._opcode === 0) {
          if (compressed) {
            const error = this.createError(
              RangeError,
              "RSV1 must be clear",
              true,
              1002,
              "WS_ERR_UNEXPECTED_RSV_1"
            );
            cb(error);
            return;
          }
          if (!this._fragmented) {
            const error = this.createError(
              RangeError,
              "invalid opcode 0",
              true,
              1002,
              "WS_ERR_INVALID_OPCODE"
            );
            cb(error);
            return;
          }
          this._opcode = this._fragmented;
        } else if (this._opcode === 1 || this._opcode === 2) {
          if (this._fragmented) {
            const error = this.createError(
              RangeError,
              `invalid opcode ${this._opcode}`,
              true,
              1002,
              "WS_ERR_INVALID_OPCODE"
            );
            cb(error);
            return;
          }
          this._compressed = compressed;
        } else if (this._opcode > 7 && this._opcode < 11) {
          if (!this._fin) {
            const error = this.createError(
              RangeError,
              "FIN must be set",
              true,
              1002,
              "WS_ERR_EXPECTED_FIN"
            );
            cb(error);
            return;
          }
          if (compressed) {
            const error = this.createError(
              RangeError,
              "RSV1 must be clear",
              true,
              1002,
              "WS_ERR_UNEXPECTED_RSV_1"
            );
            cb(error);
            return;
          }
          if (this._payloadLength > 125 || this._opcode === 8 && this._payloadLength === 1) {
            const error = this.createError(
              RangeError,
              `invalid payload length ${this._payloadLength}`,
              true,
              1002,
              "WS_ERR_INVALID_CONTROL_PAYLOAD_LENGTH"
            );
            cb(error);
            return;
          }
        } else {
          const error = this.createError(
            RangeError,
            `invalid opcode ${this._opcode}`,
            true,
            1002,
            "WS_ERR_INVALID_OPCODE"
          );
          cb(error);
          return;
        }
        if (!this._fin && !this._fragmented) this._fragmented = this._opcode;
        this._masked = (buf[1] & 128) === 128;
        if (this._isServer) {
          if (!this._masked) {
            const error = this.createError(
              RangeError,
              "MASK must be set",
              true,
              1002,
              "WS_ERR_EXPECTED_MASK"
            );
            cb(error);
            return;
          }
        } else if (this._masked) {
          const error = this.createError(
            RangeError,
            "MASK must be clear",
            true,
            1002,
            "WS_ERR_UNEXPECTED_MASK"
          );
          cb(error);
          return;
        }
        if (this._payloadLength === 126) this._state = GET_PAYLOAD_LENGTH_16;
        else if (this._payloadLength === 127) this._state = GET_PAYLOAD_LENGTH_64;
        else this.haveLength(cb);
      }
      /**
       * Gets extended payload length (7+16).
       *
       * @param {Function} cb Callback
       * @private
       */
      getPayloadLength16(cb) {
        if (this._bufferedBytes < 2) {
          this._loop = false;
          return;
        }
        this._payloadLength = this.consume(2).readUInt16BE(0);
        this.haveLength(cb);
      }
      /**
       * Gets extended payload length (7+64).
       *
       * @param {Function} cb Callback
       * @private
       */
      getPayloadLength64(cb) {
        if (this._bufferedBytes < 8) {
          this._loop = false;
          return;
        }
        const buf = this.consume(8);
        const num = buf.readUInt32BE(0);
        if (num > Math.pow(2, 53 - 32) - 1) {
          const error = this.createError(
            RangeError,
            "Unsupported WebSocket frame: payload length > 2^53 - 1",
            false,
            1009,
            "WS_ERR_UNSUPPORTED_DATA_PAYLOAD_LENGTH"
          );
          cb(error);
          return;
        }
        this._payloadLength = num * Math.pow(2, 32) + buf.readUInt32BE(4);
        this.haveLength(cb);
      }
      /**
       * Payload length has been read.
       *
       * @param {Function} cb Callback
       * @private
       */
      haveLength(cb) {
        if (this._payloadLength && this._opcode < 8) {
          this._totalPayloadLength += this._payloadLength;
          if (this._totalPayloadLength > this._maxPayload && this._maxPayload > 0) {
            const error = this.createError(
              RangeError,
              "Max payload size exceeded",
              false,
              1009,
              "WS_ERR_UNSUPPORTED_MESSAGE_LENGTH"
            );
            cb(error);
            return;
          }
        }
        if (this._masked) this._state = GET_MASK;
        else this._state = GET_DATA;
      }
      /**
       * Reads mask bytes.
       *
       * @private
       */
      getMask() {
        if (this._bufferedBytes < 4) {
          this._loop = false;
          return;
        }
        this._mask = this.consume(4);
        this._state = GET_DATA;
      }
      /**
       * Reads data bytes.
       *
       * @param {Function} cb Callback
       * @private
       */
      getData(cb) {
        let data2 = EMPTY_BUFFER;
        if (this._payloadLength) {
          if (this._bufferedBytes < this._payloadLength) {
            this._loop = false;
            return;
          }
          data2 = this.consume(this._payloadLength);
          if (this._masked && (this._mask[0] | this._mask[1] | this._mask[2] | this._mask[3]) !== 0) {
            unmask(data2, this._mask);
          }
        }
        if (this._opcode > 7) {
          this.controlMessage(data2, cb);
          return;
        }
        if (this._maxFragments > 0 && ++this._numFragments > this._maxFragments) {
          const error = this.createError(
            RangeError,
            "Too many message fragments",
            false,
            1008,
            "WS_ERR_TOO_MANY_BUFFERED_PARTS"
          );
          cb(error);
          return;
        }
        if (this._compressed) {
          this._state = INFLATING;
          this.decompress(data2, cb);
          return;
        }
        if (data2.length) {
          this._messageLength = this._totalPayloadLength;
          this._fragments.push(data2);
        }
        this.dataMessage(cb);
      }
      /**
       * Decompresses data.
       *
       * @param {Buffer} data Compressed data
       * @param {Function} cb Callback
       * @private
       */
      decompress(data2, cb) {
        const perMessageDeflate = this._extensions[PerMessageDeflate2.extensionName];
        perMessageDeflate.decompress(data2, this._fin, (err, buf) => {
          if (err) return cb(err);
          if (buf.length) {
            this._messageLength += buf.length;
            if (this._messageLength > this._maxPayload && this._maxPayload > 0) {
              const error = this.createError(
                RangeError,
                "Max payload size exceeded",
                false,
                1009,
                "WS_ERR_UNSUPPORTED_MESSAGE_LENGTH"
              );
              cb(error);
              return;
            }
            this._fragments.push(buf);
          }
          this.dataMessage(cb);
          if (this._state === GET_INFO) this.startLoop(cb);
        });
      }
      /**
       * Handles a data message.
       *
       * @param {Function} cb Callback
       * @private
       */
      dataMessage(cb) {
        if (!this._fin) {
          this._state = GET_INFO;
          return;
        }
        const messageLength = this._messageLength;
        const fragments = this._fragments;
        this._totalPayloadLength = 0;
        this._messageLength = 0;
        this._fragmented = 0;
        this._numFragments = 0;
        this._fragments = [];
        if (this._opcode === 2) {
          let data2;
          if (this._binaryType === "nodebuffer") {
            data2 = concat(fragments, messageLength);
          } else if (this._binaryType === "arraybuffer") {
            data2 = toArrayBuffer(concat(fragments, messageLength));
          } else if (this._binaryType === "blob") {
            data2 = new Blob(fragments);
          } else {
            data2 = fragments;
          }
          if (this._allowSynchronousEvents) {
            this.emit("message", data2, true);
            this._state = GET_INFO;
          } else {
            this._state = DEFER_EVENT;
            setImmediate(() => {
              this.emit("message", data2, true);
              this._state = GET_INFO;
              this.startLoop(cb);
            });
          }
        } else {
          const buf = concat(fragments, messageLength);
          if (!this._skipUTF8Validation && !isValidUTF8(buf)) {
            const error = this.createError(
              Error,
              "invalid UTF-8 sequence",
              true,
              1007,
              "WS_ERR_INVALID_UTF8"
            );
            cb(error);
            return;
          }
          if (this._state === INFLATING || this._allowSynchronousEvents) {
            this.emit("message", buf, false);
            this._state = GET_INFO;
          } else {
            this._state = DEFER_EVENT;
            setImmediate(() => {
              this.emit("message", buf, false);
              this._state = GET_INFO;
              this.startLoop(cb);
            });
          }
        }
      }
      /**
       * Handles a control message.
       *
       * @param {Buffer} data Data to handle
       * @return {(Error|RangeError|undefined)} A possible error
       * @private
       */
      controlMessage(data2, cb) {
        if (this._opcode === 8) {
          if (data2.length === 0) {
            this._loop = false;
            this.emit("conclude", 1005, EMPTY_BUFFER);
            this.end();
          } else {
            const code = data2.readUInt16BE(0);
            if (!isValidStatusCode(code)) {
              const error = this.createError(
                RangeError,
                `invalid status code ${code}`,
                true,
                1002,
                "WS_ERR_INVALID_CLOSE_CODE"
              );
              cb(error);
              return;
            }
            const buf = new FastBuffer(
              data2.buffer,
              data2.byteOffset + 2,
              data2.length - 2
            );
            if (!this._skipUTF8Validation && !isValidUTF8(buf)) {
              const error = this.createError(
                Error,
                "invalid UTF-8 sequence",
                true,
                1007,
                "WS_ERR_INVALID_UTF8"
              );
              cb(error);
              return;
            }
            this._loop = false;
            this.emit("conclude", code, buf);
            this.end();
          }
          this._state = GET_INFO;
          return;
        }
        if (this._allowSynchronousEvents) {
          this.emit(this._opcode === 9 ? "ping" : "pong", data2);
          this._state = GET_INFO;
        } else {
          this._state = DEFER_EVENT;
          setImmediate(() => {
            this.emit(this._opcode === 9 ? "ping" : "pong", data2);
            this._state = GET_INFO;
            this.startLoop(cb);
          });
        }
      }
      /**
       * Builds an error object.
       *
       * @param {function(new:Error|RangeError)} ErrorCtor The error constructor
       * @param {String} message The error message
       * @param {Boolean} prefix Specifies whether or not to add a default prefix to
       *     `message`
       * @param {Number} statusCode The status code
       * @param {String} errorCode The exposed error code
       * @return {(Error|RangeError)} The error
       * @private
       */
      createError(ErrorCtor, message, prefix, statusCode, errorCode) {
        this._loop = false;
        this._errored = true;
        const err = new ErrorCtor(
          prefix ? `Invalid WebSocket frame: ${message}` : message
        );
        Error.captureStackTrace(err, this.createError);
        err.code = errorCode;
        err[kStatusCode] = statusCode;
        return err;
      }
    };
    module.exports = Receiver2;
  }
});

// ../../node_modules/ws/lib/sender.js
var require_sender = __commonJS({
  "../../node_modules/ws/lib/sender.js"(exports, module) {
    "use strict";
    var { Duplex } = __require("stream");
    var { randomFillSync } = __require("crypto");
    var {
      types: { isUint8Array }
    } = __require("util");
    var PerMessageDeflate2 = require_permessage_deflate();
    var { EMPTY_BUFFER, kWebSocket, NOOP } = require_constants();
    var { isBlob, isValidStatusCode } = require_validation();
    var { mask: applyMask, toBuffer } = require_buffer_util();
    var kByteLength = /* @__PURE__ */ Symbol("kByteLength");
    var maskBuffer = Buffer.alloc(4);
    var RANDOM_POOL_SIZE = 8 * 1024;
    var randomPool;
    var randomPoolPointer = RANDOM_POOL_SIZE;
    var DEFAULT = 0;
    var DEFLATING = 1;
    var GET_BLOB_DATA = 2;
    var Sender2 = class _Sender {
      /**
       * Creates a Sender instance.
       *
       * @param {Duplex} socket The connection socket
       * @param {Object} [extensions] An object containing the negotiated extensions
       * @param {Function} [generateMask] The function used to generate the masking
       *     key
       */
      constructor(socket, extensions, generateMask) {
        this._extensions = extensions || {};
        if (generateMask) {
          this._generateMask = generateMask;
          this._maskBuffer = Buffer.alloc(4);
        }
        this._socket = socket;
        this._firstFragment = true;
        this._compress = false;
        this._bufferedBytes = 0;
        this._queue = [];
        this._state = DEFAULT;
        this.onerror = NOOP;
        this[kWebSocket] = void 0;
      }
      /**
       * Frames a piece of data according to the HyBi WebSocket protocol.
       *
       * @param {(Buffer|String)} data The data to frame
       * @param {Object} options Options object
       * @param {Boolean} [options.fin=false] Specifies whether or not to set the
       *     FIN bit
       * @param {Function} [options.generateMask] The function used to generate the
       *     masking key
       * @param {Boolean} [options.mask=false] Specifies whether or not to mask
       *     `data`
       * @param {Buffer} [options.maskBuffer] The buffer used to store the masking
       *     key
       * @param {Number} options.opcode The opcode
       * @param {Boolean} [options.readOnly=false] Specifies whether `data` can be
       *     modified
       * @param {Boolean} [options.rsv1=false] Specifies whether or not to set the
       *     RSV1 bit
       * @return {(Buffer|String)[]} The framed data
       * @public
       */
      static frame(data2, options) {
        let mask;
        let merge = false;
        let offset = 2;
        let skipMasking = false;
        if (options.mask) {
          mask = options.maskBuffer || maskBuffer;
          if (options.generateMask) {
            options.generateMask(mask);
          } else {
            if (randomPoolPointer === RANDOM_POOL_SIZE) {
              if (randomPool === void 0) {
                randomPool = Buffer.alloc(RANDOM_POOL_SIZE);
              }
              randomFillSync(randomPool, 0, RANDOM_POOL_SIZE);
              randomPoolPointer = 0;
            }
            mask[0] = randomPool[randomPoolPointer++];
            mask[1] = randomPool[randomPoolPointer++];
            mask[2] = randomPool[randomPoolPointer++];
            mask[3] = randomPool[randomPoolPointer++];
          }
          skipMasking = (mask[0] | mask[1] | mask[2] | mask[3]) === 0;
          offset = 6;
        }
        let dataLength;
        if (typeof data2 === "string") {
          if ((!options.mask || skipMasking) && options[kByteLength] !== void 0) {
            dataLength = options[kByteLength];
          } else {
            data2 = Buffer.from(data2);
            dataLength = data2.length;
          }
        } else {
          dataLength = data2.length;
          merge = options.mask && options.readOnly && !skipMasking;
        }
        let payloadLength = dataLength;
        if (dataLength >= 65536) {
          offset += 8;
          payloadLength = 127;
        } else if (dataLength > 125) {
          offset += 2;
          payloadLength = 126;
        }
        const target = Buffer.allocUnsafe(merge ? dataLength + offset : offset);
        target[0] = options.fin ? options.opcode | 128 : options.opcode;
        if (options.rsv1) target[0] |= 64;
        target[1] = payloadLength;
        if (payloadLength === 126) {
          target.writeUInt16BE(dataLength, 2);
        } else if (payloadLength === 127) {
          target[2] = target[3] = 0;
          target.writeUIntBE(dataLength, 4, 6);
        }
        if (!options.mask) return [target, data2];
        target[1] |= 128;
        target[offset - 4] = mask[0];
        target[offset - 3] = mask[1];
        target[offset - 2] = mask[2];
        target[offset - 1] = mask[3];
        if (skipMasking) return [target, data2];
        if (merge) {
          applyMask(data2, mask, target, offset, dataLength);
          return [target];
        }
        applyMask(data2, mask, data2, 0, dataLength);
        return [target, data2];
      }
      /**
       * Sends a close message to the other peer.
       *
       * @param {Number} [code] The status code component of the body
       * @param {(String|Buffer)} [data] The message component of the body
       * @param {Boolean} [mask=false] Specifies whether or not to mask the message
       * @param {Function} [cb] Callback
       * @public
       */
      close(code, data2, mask, cb) {
        let buf;
        if (code === void 0) {
          buf = EMPTY_BUFFER;
        } else if (typeof code !== "number" || !isValidStatusCode(code)) {
          throw new TypeError("First argument must be a valid error code number");
        } else if (data2 === void 0 || !data2.length) {
          buf = Buffer.allocUnsafe(2);
          buf.writeUInt16BE(code, 0);
        } else {
          const length = Buffer.byteLength(data2);
          if (length > 123) {
            throw new RangeError("The message must not be greater than 123 bytes");
          }
          buf = Buffer.allocUnsafe(2 + length);
          buf.writeUInt16BE(code, 0);
          if (typeof data2 === "string") {
            buf.write(data2, 2);
          } else if (isUint8Array(data2)) {
            buf.set(data2, 2);
          } else {
            throw new TypeError("Second argument must be a string or a Uint8Array");
          }
        }
        const options = {
          [kByteLength]: buf.length,
          fin: true,
          generateMask: this._generateMask,
          mask,
          maskBuffer: this._maskBuffer,
          opcode: 8,
          readOnly: false,
          rsv1: false
        };
        if (this._state !== DEFAULT) {
          this.enqueue([this.dispatch, buf, false, options, cb]);
        } else {
          this.sendFrame(_Sender.frame(buf, options), cb);
        }
      }
      /**
       * Sends a ping message to the other peer.
       *
       * @param {*} data The message to send
       * @param {Boolean} [mask=false] Specifies whether or not to mask `data`
       * @param {Function} [cb] Callback
       * @public
       */
      ping(data2, mask, cb) {
        let byteLength;
        let readOnly;
        if (typeof data2 === "string") {
          byteLength = Buffer.byteLength(data2);
          readOnly = false;
        } else if (isBlob(data2)) {
          byteLength = data2.size;
          readOnly = false;
        } else {
          data2 = toBuffer(data2);
          byteLength = data2.length;
          readOnly = toBuffer.readOnly;
        }
        if (byteLength > 125) {
          throw new RangeError("The data size must not be greater than 125 bytes");
        }
        const options = {
          [kByteLength]: byteLength,
          fin: true,
          generateMask: this._generateMask,
          mask,
          maskBuffer: this._maskBuffer,
          opcode: 9,
          readOnly,
          rsv1: false
        };
        if (isBlob(data2)) {
          if (this._state !== DEFAULT) {
            this.enqueue([this.getBlobData, data2, false, options, cb]);
          } else {
            this.getBlobData(data2, false, options, cb);
          }
        } else if (this._state !== DEFAULT) {
          this.enqueue([this.dispatch, data2, false, options, cb]);
        } else {
          this.sendFrame(_Sender.frame(data2, options), cb);
        }
      }
      /**
       * Sends a pong message to the other peer.
       *
       * @param {*} data The message to send
       * @param {Boolean} [mask=false] Specifies whether or not to mask `data`
       * @param {Function} [cb] Callback
       * @public
       */
      pong(data2, mask, cb) {
        let byteLength;
        let readOnly;
        if (typeof data2 === "string") {
          byteLength = Buffer.byteLength(data2);
          readOnly = false;
        } else if (isBlob(data2)) {
          byteLength = data2.size;
          readOnly = false;
        } else {
          data2 = toBuffer(data2);
          byteLength = data2.length;
          readOnly = toBuffer.readOnly;
        }
        if (byteLength > 125) {
          throw new RangeError("The data size must not be greater than 125 bytes");
        }
        const options = {
          [kByteLength]: byteLength,
          fin: true,
          generateMask: this._generateMask,
          mask,
          maskBuffer: this._maskBuffer,
          opcode: 10,
          readOnly,
          rsv1: false
        };
        if (isBlob(data2)) {
          if (this._state !== DEFAULT) {
            this.enqueue([this.getBlobData, data2, false, options, cb]);
          } else {
            this.getBlobData(data2, false, options, cb);
          }
        } else if (this._state !== DEFAULT) {
          this.enqueue([this.dispatch, data2, false, options, cb]);
        } else {
          this.sendFrame(_Sender.frame(data2, options), cb);
        }
      }
      /**
       * Sends a data message to the other peer.
       *
       * @param {*} data The message to send
       * @param {Object} options Options object
       * @param {Boolean} [options.binary=false] Specifies whether `data` is binary
       *     or text
       * @param {Boolean} [options.compress=false] Specifies whether or not to
       *     compress `data`
       * @param {Boolean} [options.fin=false] Specifies whether the fragment is the
       *     last one
       * @param {Boolean} [options.mask=false] Specifies whether or not to mask
       *     `data`
       * @param {Function} [cb] Callback
       * @public
       */
      send(data2, options, cb) {
        const perMessageDeflate = this._extensions[PerMessageDeflate2.extensionName];
        let opcode = options.binary ? 2 : 1;
        let rsv1 = options.compress;
        let byteLength;
        let readOnly;
        if (typeof data2 === "string") {
          byteLength = Buffer.byteLength(data2);
          readOnly = false;
        } else if (isBlob(data2)) {
          byteLength = data2.size;
          readOnly = false;
        } else {
          data2 = toBuffer(data2);
          byteLength = data2.length;
          readOnly = toBuffer.readOnly;
        }
        if (this._firstFragment) {
          this._firstFragment = false;
          if (rsv1 && perMessageDeflate && perMessageDeflate.params[perMessageDeflate._isServer ? "server_no_context_takeover" : "client_no_context_takeover"]) {
            rsv1 = byteLength >= perMessageDeflate._threshold;
          }
          this._compress = rsv1;
        } else {
          rsv1 = false;
          opcode = 0;
        }
        if (options.fin) this._firstFragment = true;
        const opts = {
          [kByteLength]: byteLength,
          fin: options.fin,
          generateMask: this._generateMask,
          mask: options.mask,
          maskBuffer: this._maskBuffer,
          opcode,
          readOnly,
          rsv1
        };
        if (isBlob(data2)) {
          if (this._state !== DEFAULT) {
            this.enqueue([this.getBlobData, data2, this._compress, opts, cb]);
          } else {
            this.getBlobData(data2, this._compress, opts, cb);
          }
        } else if (this._state !== DEFAULT) {
          this.enqueue([this.dispatch, data2, this._compress, opts, cb]);
        } else {
          this.dispatch(data2, this._compress, opts, cb);
        }
      }
      /**
       * Gets the contents of a blob as binary data.
       *
       * @param {Blob} blob The blob
       * @param {Boolean} [compress=false] Specifies whether or not to compress
       *     the data
       * @param {Object} options Options object
       * @param {Boolean} [options.fin=false] Specifies whether or not to set the
       *     FIN bit
       * @param {Function} [options.generateMask] The function used to generate the
       *     masking key
       * @param {Boolean} [options.mask=false] Specifies whether or not to mask
       *     `data`
       * @param {Buffer} [options.maskBuffer] The buffer used to store the masking
       *     key
       * @param {Number} options.opcode The opcode
       * @param {Boolean} [options.readOnly=false] Specifies whether `data` can be
       *     modified
       * @param {Boolean} [options.rsv1=false] Specifies whether or not to set the
       *     RSV1 bit
       * @param {Function} [cb] Callback
       * @private
       */
      getBlobData(blob, compress, options, cb) {
        this._bufferedBytes += options[kByteLength];
        this._state = GET_BLOB_DATA;
        blob.arrayBuffer().then((arrayBuffer) => {
          if (this._socket.destroyed) {
            const err = new Error(
              "The socket was closed while the blob was being read"
            );
            process.nextTick(callCallbacks, this, err, cb);
            return;
          }
          this._bufferedBytes -= options[kByteLength];
          const data2 = toBuffer(arrayBuffer);
          if (!compress) {
            this._state = DEFAULT;
            this.sendFrame(_Sender.frame(data2, options), cb);
            this.dequeue();
          } else {
            this.dispatch(data2, compress, options, cb);
          }
        }).catch((err) => {
          process.nextTick(onError, this, err, cb);
        });
      }
      /**
       * Dispatches a message.
       *
       * @param {(Buffer|String)} data The message to send
       * @param {Boolean} [compress=false] Specifies whether or not to compress
       *     `data`
       * @param {Object} options Options object
       * @param {Boolean} [options.fin=false] Specifies whether or not to set the
       *     FIN bit
       * @param {Function} [options.generateMask] The function used to generate the
       *     masking key
       * @param {Boolean} [options.mask=false] Specifies whether or not to mask
       *     `data`
       * @param {Buffer} [options.maskBuffer] The buffer used to store the masking
       *     key
       * @param {Number} options.opcode The opcode
       * @param {Boolean} [options.readOnly=false] Specifies whether `data` can be
       *     modified
       * @param {Boolean} [options.rsv1=false] Specifies whether or not to set the
       *     RSV1 bit
       * @param {Function} [cb] Callback
       * @private
       */
      dispatch(data2, compress, options, cb) {
        if (!compress) {
          this.sendFrame(_Sender.frame(data2, options), cb);
          return;
        }
        const perMessageDeflate = this._extensions[PerMessageDeflate2.extensionName];
        this._bufferedBytes += options[kByteLength];
        this._state = DEFLATING;
        perMessageDeflate.compress(data2, options.fin, (_, buf) => {
          if (this._socket.destroyed) {
            const err = new Error(
              "The socket was closed while data was being compressed"
            );
            callCallbacks(this, err, cb);
            return;
          }
          this._bufferedBytes -= options[kByteLength];
          this._state = DEFAULT;
          options.readOnly = false;
          this.sendFrame(_Sender.frame(buf, options), cb);
          this.dequeue();
        });
      }
      /**
       * Executes queued send operations.
       *
       * @private
       */
      dequeue() {
        while (this._state === DEFAULT && this._queue.length) {
          const params = this._queue.shift();
          this._bufferedBytes -= params[3][kByteLength];
          Reflect.apply(params[0], this, params.slice(1));
        }
      }
      /**
       * Enqueues a send operation.
       *
       * @param {Array} params Send operation parameters.
       * @private
       */
      enqueue(params) {
        this._bufferedBytes += params[3][kByteLength];
        this._queue.push(params);
      }
      /**
       * Sends a frame.
       *
       * @param {(Buffer | String)[]} list The frame to send
       * @param {Function} [cb] Callback
       * @private
       */
      sendFrame(list2, cb) {
        if (list2.length === 2) {
          this._socket.cork();
          this._socket.write(list2[0]);
          this._socket.write(list2[1], cb);
          this._socket.uncork();
        } else {
          this._socket.write(list2[0], cb);
        }
      }
    };
    module.exports = Sender2;
    function callCallbacks(sender, err, cb) {
      if (typeof cb === "function") cb(err);
      for (let i = 0; i < sender._queue.length; i++) {
        const params = sender._queue[i];
        const callback = params[params.length - 1];
        if (typeof callback === "function") callback(err);
      }
    }
    function onError(sender, err, cb) {
      callCallbacks(sender, err, cb);
      sender.onerror(err);
    }
  }
});

// ../../node_modules/ws/lib/event-target.js
var require_event_target = __commonJS({
  "../../node_modules/ws/lib/event-target.js"(exports, module) {
    "use strict";
    var { kForOnEventAttribute, kListener } = require_constants();
    var kCode = /* @__PURE__ */ Symbol("kCode");
    var kData = /* @__PURE__ */ Symbol("kData");
    var kError = /* @__PURE__ */ Symbol("kError");
    var kMessage = /* @__PURE__ */ Symbol("kMessage");
    var kReason = /* @__PURE__ */ Symbol("kReason");
    var kTarget = /* @__PURE__ */ Symbol("kTarget");
    var kType = /* @__PURE__ */ Symbol("kType");
    var kWasClean = /* @__PURE__ */ Symbol("kWasClean");
    var Event = class {
      /**
       * Create a new `Event`.
       *
       * @param {String} type The name of the event
       * @throws {TypeError} If the `type` argument is not specified
       */
      constructor(type) {
        this[kTarget] = null;
        this[kType] = type;
      }
      /**
       * @type {*}
       */
      get target() {
        return this[kTarget];
      }
      /**
       * @type {String}
       */
      get type() {
        return this[kType];
      }
    };
    Object.defineProperty(Event.prototype, "target", { enumerable: true });
    Object.defineProperty(Event.prototype, "type", { enumerable: true });
    var CloseEvent = class extends Event {
      /**
       * Create a new `CloseEvent`.
       *
       * @param {String} type The name of the event
       * @param {Object} [options] A dictionary object that allows for setting
       *     attributes via object members of the same name
       * @param {Number} [options.code=0] The status code explaining why the
       *     connection was closed
       * @param {String} [options.reason=''] A human-readable string explaining why
       *     the connection was closed
       * @param {Boolean} [options.wasClean=false] Indicates whether or not the
       *     connection was cleanly closed
       */
      constructor(type, options = {}) {
        super(type);
        this[kCode] = options.code === void 0 ? 0 : options.code;
        this[kReason] = options.reason === void 0 ? "" : options.reason;
        this[kWasClean] = options.wasClean === void 0 ? false : options.wasClean;
      }
      /**
       * @type {Number}
       */
      get code() {
        return this[kCode];
      }
      /**
       * @type {String}
       */
      get reason() {
        return this[kReason];
      }
      /**
       * @type {Boolean}
       */
      get wasClean() {
        return this[kWasClean];
      }
    };
    Object.defineProperty(CloseEvent.prototype, "code", { enumerable: true });
    Object.defineProperty(CloseEvent.prototype, "reason", { enumerable: true });
    Object.defineProperty(CloseEvent.prototype, "wasClean", { enumerable: true });
    var ErrorEvent = class extends Event {
      /**
       * Create a new `ErrorEvent`.
       *
       * @param {String} type The name of the event
       * @param {Object} [options] A dictionary object that allows for setting
       *     attributes via object members of the same name
       * @param {*} [options.error=null] The error that generated this event
       * @param {String} [options.message=''] The error message
       */
      constructor(type, options = {}) {
        super(type);
        this[kError] = options.error === void 0 ? null : options.error;
        this[kMessage] = options.message === void 0 ? "" : options.message;
      }
      /**
       * @type {*}
       */
      get error() {
        return this[kError];
      }
      /**
       * @type {String}
       */
      get message() {
        return this[kMessage];
      }
    };
    Object.defineProperty(ErrorEvent.prototype, "error", { enumerable: true });
    Object.defineProperty(ErrorEvent.prototype, "message", { enumerable: true });
    var MessageEvent = class extends Event {
      /**
       * Create a new `MessageEvent`.
       *
       * @param {String} type The name of the event
       * @param {Object} [options] A dictionary object that allows for setting
       *     attributes via object members of the same name
       * @param {*} [options.data=null] The message content
       */
      constructor(type, options = {}) {
        super(type);
        this[kData] = options.data === void 0 ? null : options.data;
      }
      /**
       * @type {*}
       */
      get data() {
        return this[kData];
      }
    };
    Object.defineProperty(MessageEvent.prototype, "data", { enumerable: true });
    var EventTarget = {
      /**
       * Register an event listener.
       *
       * @param {String} type A string representing the event type to listen for
       * @param {(Function|Object)} handler The listener to add
       * @param {Object} [options] An options object specifies characteristics about
       *     the event listener
       * @param {Boolean} [options.once=false] A `Boolean` indicating that the
       *     listener should be invoked at most once after being added. If `true`,
       *     the listener would be automatically removed when invoked.
       * @public
       */
      addEventListener(type, handler, options = {}) {
        for (const listener of this.listeners(type)) {
          if (!options[kForOnEventAttribute] && listener[kListener] === handler && !listener[kForOnEventAttribute]) {
            return;
          }
        }
        let wrapper;
        if (type === "message") {
          wrapper = function onMessage(data2, isBinary) {
            const event = new MessageEvent("message", {
              data: isBinary ? data2 : data2.toString()
            });
            event[kTarget] = this;
            callListener(handler, this, event);
          };
        } else if (type === "close") {
          wrapper = function onClose(code, message) {
            const event = new CloseEvent("close", {
              code,
              reason: message.toString(),
              wasClean: this._closeFrameReceived && this._closeFrameSent
            });
            event[kTarget] = this;
            callListener(handler, this, event);
          };
        } else if (type === "error") {
          wrapper = function onError(error) {
            const event = new ErrorEvent("error", {
              error,
              message: error.message
            });
            event[kTarget] = this;
            callListener(handler, this, event);
          };
        } else if (type === "open") {
          wrapper = function onOpen() {
            const event = new Event("open");
            event[kTarget] = this;
            callListener(handler, this, event);
          };
        } else {
          return;
        }
        wrapper[kForOnEventAttribute] = !!options[kForOnEventAttribute];
        wrapper[kListener] = handler;
        if (options.once) {
          this.once(type, wrapper);
        } else {
          this.on(type, wrapper);
        }
      },
      /**
       * Remove an event listener.
       *
       * @param {String} type A string representing the event type to remove
       * @param {(Function|Object)} handler The listener to remove
       * @public
       */
      removeEventListener(type, handler) {
        for (const listener of this.listeners(type)) {
          if (listener[kListener] === handler && !listener[kForOnEventAttribute]) {
            this.removeListener(type, listener);
            break;
          }
        }
      }
    };
    module.exports = {
      CloseEvent,
      ErrorEvent,
      Event,
      EventTarget,
      MessageEvent
    };
    function callListener(listener, thisArg, event) {
      if (typeof listener === "object" && listener.handleEvent) {
        listener.handleEvent.call(listener, event);
      } else {
        listener.call(thisArg, event);
      }
    }
  }
});

// ../../node_modules/ws/lib/extension.js
var require_extension = __commonJS({
  "../../node_modules/ws/lib/extension.js"(exports, module) {
    "use strict";
    var { tokenChars } = require_validation();
    function push(dest, name, elem) {
      if (dest[name] === void 0) dest[name] = [elem];
      else dest[name].push(elem);
    }
    function parse(header) {
      const offers = /* @__PURE__ */ Object.create(null);
      let params = /* @__PURE__ */ Object.create(null);
      let mustUnescape = false;
      let isEscaping = false;
      let inQuotes = false;
      let extensionName;
      let paramName;
      let start = -1;
      let code = -1;
      let end = -1;
      let i = 0;
      for (; i < header.length; i++) {
        code = header.charCodeAt(i);
        if (extensionName === void 0) {
          if (end === -1 && tokenChars[code] === 1) {
            if (start === -1) start = i;
          } else if (i !== 0 && (code === 32 || code === 9)) {
            if (end === -1 && start !== -1) end = i;
          } else if (code === 59 || code === 44) {
            if (start === -1) {
              throw new SyntaxError(`Unexpected character at index ${i}`);
            }
            if (end === -1) end = i;
            const name = header.slice(start, end);
            if (code === 44) {
              push(offers, name, params);
              params = /* @__PURE__ */ Object.create(null);
            } else {
              extensionName = name;
            }
            start = end = -1;
          } else {
            throw new SyntaxError(`Unexpected character at index ${i}`);
          }
        } else if (paramName === void 0) {
          if (end === -1 && tokenChars[code] === 1) {
            if (start === -1) start = i;
          } else if (code === 32 || code === 9) {
            if (end === -1 && start !== -1) end = i;
          } else if (code === 59 || code === 44) {
            if (start === -1) {
              throw new SyntaxError(`Unexpected character at index ${i}`);
            }
            if (end === -1) end = i;
            push(params, header.slice(start, end), true);
            if (code === 44) {
              push(offers, extensionName, params);
              params = /* @__PURE__ */ Object.create(null);
              extensionName = void 0;
            }
            start = end = -1;
          } else if (code === 61 && start !== -1 && end === -1) {
            paramName = header.slice(start, i);
            start = end = -1;
          } else {
            throw new SyntaxError(`Unexpected character at index ${i}`);
          }
        } else {
          if (isEscaping) {
            if (tokenChars[code] !== 1) {
              throw new SyntaxError(`Unexpected character at index ${i}`);
            }
            if (start === -1) start = i;
            else if (!mustUnescape) mustUnescape = true;
            isEscaping = false;
          } else if (inQuotes) {
            if (tokenChars[code] === 1) {
              if (start === -1) start = i;
            } else if (code === 34 && start !== -1) {
              inQuotes = false;
              end = i;
            } else if (code === 92) {
              isEscaping = true;
            } else {
              throw new SyntaxError(`Unexpected character at index ${i}`);
            }
          } else if (code === 34 && header.charCodeAt(i - 1) === 61) {
            inQuotes = true;
          } else if (end === -1 && tokenChars[code] === 1) {
            if (start === -1) start = i;
          } else if (start !== -1 && (code === 32 || code === 9)) {
            if (end === -1) end = i;
          } else if (code === 59 || code === 44) {
            if (start === -1) {
              throw new SyntaxError(`Unexpected character at index ${i}`);
            }
            if (end === -1) end = i;
            let value = header.slice(start, end);
            if (mustUnescape) {
              value = value.replace(/\\/g, "");
              mustUnescape = false;
            }
            push(params, paramName, value);
            if (code === 44) {
              push(offers, extensionName, params);
              params = /* @__PURE__ */ Object.create(null);
              extensionName = void 0;
            }
            paramName = void 0;
            start = end = -1;
          } else {
            throw new SyntaxError(`Unexpected character at index ${i}`);
          }
        }
      }
      if (start === -1 || inQuotes || code === 32 || code === 9) {
        throw new SyntaxError("Unexpected end of input");
      }
      if (end === -1) end = i;
      const token = header.slice(start, end);
      if (extensionName === void 0) {
        push(offers, token, params);
      } else {
        if (paramName === void 0) {
          push(params, token, true);
        } else if (mustUnescape) {
          push(params, paramName, token.replace(/\\/g, ""));
        } else {
          push(params, paramName, token);
        }
        push(offers, extensionName, params);
      }
      return offers;
    }
    function format(extensions) {
      return Object.keys(extensions).map((extension2) => {
        let configurations = extensions[extension2];
        if (!Array.isArray(configurations)) configurations = [configurations];
        return configurations.map((params) => {
          return [extension2].concat(
            Object.keys(params).map((k) => {
              let values = params[k];
              if (!Array.isArray(values)) values = [values];
              return values.map((v) => v === true ? k : `${k}=${v}`).join("; ");
            })
          ).join("; ");
        }).join(", ");
      }).join(", ");
    }
    module.exports = { format, parse };
  }
});

// ../../node_modules/ws/lib/websocket.js
var require_websocket = __commonJS({
  "../../node_modules/ws/lib/websocket.js"(exports, module) {
    "use strict";
    var EventEmitter = __require("events");
    var https = __require("https");
    var http = __require("http");
    var net = __require("net");
    var tls = __require("tls");
    var { randomBytes, createHash: createHash2 } = __require("crypto");
    var { Duplex, Readable } = __require("stream");
    var { URL: URL2 } = __require("url");
    var PerMessageDeflate2 = require_permessage_deflate();
    var Receiver2 = require_receiver();
    var Sender2 = require_sender();
    var { isBlob } = require_validation();
    var {
      BINARY_TYPES,
      CLOSE_TIMEOUT,
      EMPTY_BUFFER,
      GUID,
      kForOnEventAttribute,
      kListener,
      kStatusCode,
      kWebSocket,
      NOOP
    } = require_constants();
    var {
      EventTarget: { addEventListener, removeEventListener }
    } = require_event_target();
    var { format, parse } = require_extension();
    var { toBuffer } = require_buffer_util();
    var kAborted = /* @__PURE__ */ Symbol("kAborted");
    var protocolVersions = [8, 13];
    var readyStates = ["CONNECTING", "OPEN", "CLOSING", "CLOSED"];
    var subprotocolRegex = /^[!#$%&'*+\-.0-9A-Z^_`|a-z~]+$/;
    var WebSocket3 = class _WebSocket extends EventEmitter {
      /**
       * Create a new `WebSocket`.
       *
       * @param {(String|URL)} address The URL to which to connect
       * @param {(String|String[])} [protocols] The subprotocols
       * @param {Object} [options] Connection options
       */
      constructor(address2, protocols, options) {
        super();
        this._binaryType = BINARY_TYPES[0];
        this._closeCode = 1006;
        this._closeFrameReceived = false;
        this._closeFrameSent = false;
        this._closeMessage = EMPTY_BUFFER;
        this._closeTimer = null;
        this._errorEmitted = false;
        this._extensions = {};
        this._paused = false;
        this._protocol = "";
        this._readyState = _WebSocket.CONNECTING;
        this._receiver = null;
        this._sender = null;
        this._socket = null;
        if (address2 !== null) {
          this._bufferedAmount = 0;
          this._isServer = false;
          this._redirects = 0;
          if (protocols === void 0) {
            if (!options || options.protocols === void 0) {
              protocols = [];
            } else if (Array.isArray(options.protocols)) {
              protocols = options.protocols;
            } else {
              protocols = [options.protocols];
            }
          } else if (!Array.isArray(protocols)) {
            if (typeof protocols === "object" && protocols !== null) {
              options = protocols;
              if (options.protocols === void 0) {
                protocols = [];
              } else if (Array.isArray(options.protocols)) {
                protocols = options.protocols;
              } else {
                protocols = [options.protocols];
              }
            } else {
              protocols = [protocols];
            }
          }
          initAsClient(this, address2, protocols, options);
        } else {
          this._autoPong = options.autoPong;
          this._closeTimeout = options.closeTimeout;
          this._isServer = true;
        }
      }
      /**
       * For historical reasons, the custom "nodebuffer" type is used by the default
       * instead of "blob".
       *
       * @type {String}
       */
      get binaryType() {
        return this._binaryType;
      }
      set binaryType(type) {
        if (!BINARY_TYPES.includes(type)) return;
        this._binaryType = type;
        if (this._receiver) this._receiver._binaryType = type;
      }
      /**
       * @type {Number}
       */
      get bufferedAmount() {
        if (!this._socket) return this._bufferedAmount;
        return this._socket._writableState.length + this._sender._bufferedBytes;
      }
      /**
       * @type {String}
       */
      get extensions() {
        return Object.keys(this._extensions).join();
      }
      /**
       * @type {Boolean}
       */
      get isPaused() {
        return this._paused;
      }
      /**
       * @type {Function}
       */
      /* istanbul ignore next */
      get onclose() {
        return null;
      }
      /**
       * @type {Function}
       */
      /* istanbul ignore next */
      get onerror() {
        return null;
      }
      /**
       * @type {Function}
       */
      /* istanbul ignore next */
      get onopen() {
        return null;
      }
      /**
       * @type {Function}
       */
      /* istanbul ignore next */
      get onmessage() {
        return null;
      }
      /**
       * @type {String}
       */
      get protocol() {
        return this._protocol;
      }
      /**
       * @type {Number}
       */
      get readyState() {
        return this._readyState;
      }
      /**
       * @type {String}
       */
      get url() {
        return this._url;
      }
      /**
       * Set up the socket and the internal resources.
       *
       * @param {Duplex} socket The network socket between the server and client
       * @param {Buffer} head The first packet of the upgraded stream
       * @param {Object} options Options object
       * @param {Boolean} [options.allowSynchronousEvents=false] Specifies whether
       *     any of the `'message'`, `'ping'`, and `'pong'` events can be emitted
       *     multiple times in the same tick
       * @param {Function} [options.generateMask] The function used to generate the
       *     masking key
       * @param {Number} [options.maxBufferedChunks=0] The maximum number of
       *     buffered data chunks
       * @param {Number} [options.maxFragments=0] The maximum number of message
       *     fragments
       * @param {Number} [options.maxPayload=0] The maximum allowed message size
       * @param {Boolean} [options.skipUTF8Validation=false] Specifies whether or
       *     not to skip UTF-8 validation for text and close messages
       * @private
       */
      setSocket(socket, head, options) {
        const receiver = new Receiver2({
          allowSynchronousEvents: options.allowSynchronousEvents,
          binaryType: this.binaryType,
          extensions: this._extensions,
          isServer: this._isServer,
          maxBufferedChunks: options.maxBufferedChunks,
          maxFragments: options.maxFragments,
          maxPayload: options.maxPayload,
          skipUTF8Validation: options.skipUTF8Validation
        });
        const sender = new Sender2(socket, this._extensions, options.generateMask);
        this._receiver = receiver;
        this._sender = sender;
        this._socket = socket;
        receiver[kWebSocket] = this;
        sender[kWebSocket] = this;
        socket[kWebSocket] = this;
        receiver.on("conclude", receiverOnConclude);
        receiver.on("drain", receiverOnDrain);
        receiver.on("error", receiverOnError);
        receiver.on("message", receiverOnMessage);
        receiver.on("ping", receiverOnPing);
        receiver.on("pong", receiverOnPong);
        sender.onerror = senderOnError;
        if (socket.setTimeout) socket.setTimeout(0);
        if (socket.setNoDelay) socket.setNoDelay();
        if (head.length > 0) socket.unshift(head);
        socket.on("close", socketOnClose);
        socket.on("data", socketOnData);
        socket.on("end", socketOnEnd);
        socket.on("error", socketOnError);
        this._readyState = _WebSocket.OPEN;
        this.emit("open");
      }
      /**
       * Emit the `'close'` event.
       *
       * @private
       */
      emitClose() {
        if (!this._socket) {
          this._readyState = _WebSocket.CLOSED;
          this.emit("close", this._closeCode, this._closeMessage);
          return;
        }
        if (this._extensions[PerMessageDeflate2.extensionName]) {
          this._extensions[PerMessageDeflate2.extensionName].cleanup();
        }
        this._receiver.removeAllListeners();
        this._readyState = _WebSocket.CLOSED;
        this.emit("close", this._closeCode, this._closeMessage);
      }
      /**
       * Start a closing handshake.
       *
       *          +----------+   +-----------+   +----------+
       *     - - -|ws.close()|-->|close frame|-->|ws.close()|- - -
       *    |     +----------+   +-----------+   +----------+     |
       *          +----------+   +-----------+         |
       * CLOSING  |ws.close()|<--|close frame|<--+-----+       CLOSING
       *          +----------+   +-----------+   |
       *    |           |                        |   +---+        |
       *                +------------------------+-->|fin| - - - -
       *    |         +---+                      |   +---+
       *     - - - - -|fin|<---------------------+
       *              +---+
       *
       * @param {Number} [code] Status code explaining why the connection is closing
       * @param {(String|Buffer)} [data] The reason why the connection is
       *     closing
       * @public
       */
      close(code, data2) {
        if (this.readyState === _WebSocket.CLOSED) return;
        if (this.readyState === _WebSocket.CONNECTING) {
          const msg = "WebSocket was closed before the connection was established";
          abortHandshake(this, this._req, msg);
          return;
        }
        if (this.readyState === _WebSocket.CLOSING) {
          if (this._closeFrameSent && (this._closeFrameReceived || this._receiver._writableState.errorEmitted)) {
            this._socket.end();
          }
          return;
        }
        this._sender.close(code, data2, !this._isServer, (err) => {
          if (err) return;
          this._closeFrameSent = true;
          if (this._closeFrameReceived || this._receiver._writableState.errorEmitted) {
            this._socket.end();
          }
        });
        this._readyState = _WebSocket.CLOSING;
        setCloseTimer(this);
      }
      /**
       * Pause the socket.
       *
       * @public
       */
      pause() {
        if (this.readyState === _WebSocket.CONNECTING || this.readyState === _WebSocket.CLOSED) {
          return;
        }
        this._paused = true;
        this._socket.pause();
      }
      /**
       * Send a ping.
       *
       * @param {*} [data] The data to send
       * @param {Boolean} [mask] Indicates whether or not to mask `data`
       * @param {Function} [cb] Callback which is executed when the ping is sent
       * @public
       */
      ping(data2, mask, cb) {
        if (this.readyState === _WebSocket.CONNECTING) {
          throw new Error("WebSocket is not open: readyState 0 (CONNECTING)");
        }
        if (typeof data2 === "function") {
          cb = data2;
          data2 = mask = void 0;
        } else if (typeof mask === "function") {
          cb = mask;
          mask = void 0;
        }
        if (typeof data2 === "number") data2 = data2.toString();
        if (this.readyState !== _WebSocket.OPEN) {
          sendAfterClose(this, data2, cb);
          return;
        }
        if (mask === void 0) mask = !this._isServer;
        this._sender.ping(data2 || EMPTY_BUFFER, mask, cb);
      }
      /**
       * Send a pong.
       *
       * @param {*} [data] The data to send
       * @param {Boolean} [mask] Indicates whether or not to mask `data`
       * @param {Function} [cb] Callback which is executed when the pong is sent
       * @public
       */
      pong(data2, mask, cb) {
        if (this.readyState === _WebSocket.CONNECTING) {
          throw new Error("WebSocket is not open: readyState 0 (CONNECTING)");
        }
        if (typeof data2 === "function") {
          cb = data2;
          data2 = mask = void 0;
        } else if (typeof mask === "function") {
          cb = mask;
          mask = void 0;
        }
        if (typeof data2 === "number") data2 = data2.toString();
        if (this.readyState !== _WebSocket.OPEN) {
          sendAfterClose(this, data2, cb);
          return;
        }
        if (mask === void 0) mask = !this._isServer;
        this._sender.pong(data2 || EMPTY_BUFFER, mask, cb);
      }
      /**
       * Resume the socket.
       *
       * @public
       */
      resume() {
        if (this.readyState === _WebSocket.CONNECTING || this.readyState === _WebSocket.CLOSED) {
          return;
        }
        this._paused = false;
        if (!this._receiver._writableState.needDrain) this._socket.resume();
      }
      /**
       * Send a data message.
       *
       * @param {*} data The message to send
       * @param {Object} [options] Options object
       * @param {Boolean} [options.binary] Specifies whether `data` is binary or
       *     text
       * @param {Boolean} [options.compress] Specifies whether or not to compress
       *     `data`
       * @param {Boolean} [options.fin=true] Specifies whether the fragment is the
       *     last one
       * @param {Boolean} [options.mask] Specifies whether or not to mask `data`
       * @param {Function} [cb] Callback which is executed when data is written out
       * @public
       */
      send(data2, options, cb) {
        if (this.readyState === _WebSocket.CONNECTING) {
          throw new Error("WebSocket is not open: readyState 0 (CONNECTING)");
        }
        if (typeof options === "function") {
          cb = options;
          options = {};
        }
        if (typeof data2 === "number") data2 = data2.toString();
        if (this.readyState !== _WebSocket.OPEN) {
          sendAfterClose(this, data2, cb);
          return;
        }
        const opts = {
          binary: typeof data2 !== "string",
          mask: !this._isServer,
          compress: true,
          fin: true,
          ...options
        };
        if (!this._extensions[PerMessageDeflate2.extensionName]) {
          opts.compress = false;
        }
        this._sender.send(data2 || EMPTY_BUFFER, opts, cb);
      }
      /**
       * Forcibly close the connection.
       *
       * @public
       */
      terminate() {
        if (this.readyState === _WebSocket.CLOSED) return;
        if (this.readyState === _WebSocket.CONNECTING) {
          const msg = "WebSocket was closed before the connection was established";
          abortHandshake(this, this._req, msg);
          return;
        }
        if (this._socket) {
          this._readyState = _WebSocket.CLOSING;
          this._socket.destroy();
        }
      }
    };
    Object.defineProperty(WebSocket3, "CONNECTING", {
      enumerable: true,
      value: readyStates.indexOf("CONNECTING")
    });
    Object.defineProperty(WebSocket3.prototype, "CONNECTING", {
      enumerable: true,
      value: readyStates.indexOf("CONNECTING")
    });
    Object.defineProperty(WebSocket3, "OPEN", {
      enumerable: true,
      value: readyStates.indexOf("OPEN")
    });
    Object.defineProperty(WebSocket3.prototype, "OPEN", {
      enumerable: true,
      value: readyStates.indexOf("OPEN")
    });
    Object.defineProperty(WebSocket3, "CLOSING", {
      enumerable: true,
      value: readyStates.indexOf("CLOSING")
    });
    Object.defineProperty(WebSocket3.prototype, "CLOSING", {
      enumerable: true,
      value: readyStates.indexOf("CLOSING")
    });
    Object.defineProperty(WebSocket3, "CLOSED", {
      enumerable: true,
      value: readyStates.indexOf("CLOSED")
    });
    Object.defineProperty(WebSocket3.prototype, "CLOSED", {
      enumerable: true,
      value: readyStates.indexOf("CLOSED")
    });
    [
      "binaryType",
      "bufferedAmount",
      "extensions",
      "isPaused",
      "protocol",
      "readyState",
      "url"
    ].forEach((property) => {
      Object.defineProperty(WebSocket3.prototype, property, { enumerable: true });
    });
    ["open", "error", "close", "message"].forEach((method) => {
      Object.defineProperty(WebSocket3.prototype, `on${method}`, {
        enumerable: true,
        get() {
          for (const listener of this.listeners(method)) {
            if (listener[kForOnEventAttribute]) return listener[kListener];
          }
          return null;
        },
        set(handler) {
          for (const listener of this.listeners(method)) {
            if (listener[kForOnEventAttribute]) {
              this.removeListener(method, listener);
              break;
            }
          }
          if (typeof handler !== "function") return;
          this.addEventListener(method, handler, {
            [kForOnEventAttribute]: true
          });
        }
      });
    });
    WebSocket3.prototype.addEventListener = addEventListener;
    WebSocket3.prototype.removeEventListener = removeEventListener;
    module.exports = WebSocket3;
    function initAsClient(websocket, address2, protocols, options) {
      const opts = {
        allowSynchronousEvents: true,
        autoPong: true,
        closeTimeout: CLOSE_TIMEOUT,
        protocolVersion: protocolVersions[1],
        maxBufferedChunks: 256 * 1024,
        maxFragments: 16 * 1024,
        maxPayload: 100 * 1024 * 1024,
        skipUTF8Validation: false,
        perMessageDeflate: true,
        followRedirects: false,
        maxRedirects: 10,
        ...options,
        socketPath: void 0,
        hostname: void 0,
        protocol: void 0,
        protocols: void 0,
        timeout: void 0,
        method: "GET",
        host: void 0,
        path: void 0,
        port: void 0
      };
      websocket._autoPong = opts.autoPong;
      websocket._closeTimeout = opts.closeTimeout;
      if (!protocolVersions.includes(opts.protocolVersion)) {
        throw new RangeError(
          `Unsupported protocol version: ${opts.protocolVersion} (supported versions: ${protocolVersions.join(", ")})`
        );
      }
      let parsedUrl;
      if (address2 instanceof URL2) {
        parsedUrl = address2;
      } else {
        try {
          parsedUrl = new URL2(address2);
        } catch {
          throw new SyntaxError(`Invalid URL: ${address2}`);
        }
      }
      if (parsedUrl.protocol === "http:") {
        parsedUrl.protocol = "ws:";
      } else if (parsedUrl.protocol === "https:") {
        parsedUrl.protocol = "wss:";
      }
      websocket._url = parsedUrl.href;
      const isSecure = parsedUrl.protocol === "wss:";
      const isIpcUrl = parsedUrl.protocol === "ws+unix:";
      let invalidUrlMessage;
      if (parsedUrl.protocol !== "ws:" && !isSecure && !isIpcUrl) {
        invalidUrlMessage = `The URL's protocol must be one of "ws:", "wss:", "http:", "https:", or "ws+unix:"`;
      } else if (isIpcUrl && !parsedUrl.pathname) {
        invalidUrlMessage = "The URL's pathname is empty";
      } else if (parsedUrl.hash) {
        invalidUrlMessage = "The URL contains a fragment identifier";
      }
      if (invalidUrlMessage) {
        const err = new SyntaxError(invalidUrlMessage);
        if (websocket._redirects === 0) {
          throw err;
        } else {
          emitErrorAndClose(websocket, err);
          return;
        }
      }
      const defaultPort = isSecure ? 443 : 80;
      const key = randomBytes(16).toString("base64");
      const request = isSecure ? https.request : http.request;
      const protocolSet = /* @__PURE__ */ new Set();
      let perMessageDeflate;
      opts.createConnection = opts.createConnection || (isSecure ? tlsConnect : netConnect);
      opts.defaultPort = opts.defaultPort || defaultPort;
      opts.port = parsedUrl.port || defaultPort;
      opts.host = parsedUrl.hostname.startsWith("[") ? parsedUrl.hostname.slice(1, -1) : parsedUrl.hostname;
      opts.headers = {
        ...opts.headers,
        "Sec-WebSocket-Version": opts.protocolVersion,
        "Sec-WebSocket-Key": key,
        Connection: "Upgrade",
        Upgrade: "websocket"
      };
      opts.path = parsedUrl.pathname + parsedUrl.search;
      opts.timeout = opts.handshakeTimeout;
      if (opts.perMessageDeflate) {
        perMessageDeflate = new PerMessageDeflate2({
          ...opts.perMessageDeflate,
          isServer: false,
          maxPayload: opts.maxPayload
        });
        opts.headers["Sec-WebSocket-Extensions"] = format({
          [PerMessageDeflate2.extensionName]: perMessageDeflate.offer()
        });
      }
      if (protocols.length) {
        for (const protocol of protocols) {
          if (typeof protocol !== "string" || !subprotocolRegex.test(protocol) || protocolSet.has(protocol)) {
            throw new SyntaxError(
              "An invalid or duplicated subprotocol was specified"
            );
          }
          protocolSet.add(protocol);
        }
        opts.headers["Sec-WebSocket-Protocol"] = protocols.join(",");
      }
      if (opts.origin) {
        if (opts.protocolVersion < 13) {
          opts.headers["Sec-WebSocket-Origin"] = opts.origin;
        } else {
          opts.headers.Origin = opts.origin;
        }
      }
      if (parsedUrl.username || parsedUrl.password) {
        opts.auth = `${parsedUrl.username}:${parsedUrl.password}`;
      }
      if (isIpcUrl) {
        const parts2 = opts.path.split(":");
        opts.socketPath = parts2[0];
        opts.path = parts2[1];
      }
      let req;
      if (opts.followRedirects) {
        if (websocket._redirects === 0) {
          websocket._originalIpc = isIpcUrl;
          websocket._originalSecure = isSecure;
          websocket._originalHostOrSocketPath = isIpcUrl ? opts.socketPath : parsedUrl.host;
          const headers = options && options.headers;
          options = { ...options, headers: {} };
          if (headers) {
            for (const [key2, value] of Object.entries(headers)) {
              options.headers[key2.toLowerCase()] = value;
            }
          }
        } else if (websocket.listenerCount("redirect") === 0) {
          const isSameHost = isIpcUrl ? websocket._originalIpc ? opts.socketPath === websocket._originalHostOrSocketPath : false : websocket._originalIpc ? false : parsedUrl.host === websocket._originalHostOrSocketPath;
          if (!isSameHost || websocket._originalSecure && !isSecure) {
            delete opts.headers.authorization;
            delete opts.headers.cookie;
            if (!isSameHost) delete opts.headers.host;
            opts.auth = void 0;
          }
        }
        if (opts.auth && !options.headers.authorization) {
          options.headers.authorization = "Basic " + Buffer.from(opts.auth).toString("base64");
        }
        req = websocket._req = request(opts);
        if (websocket._redirects) {
          websocket.emit("redirect", websocket.url, req);
        }
      } else {
        req = websocket._req = request(opts);
      }
      if (opts.timeout) {
        req.on("timeout", () => {
          abortHandshake(websocket, req, "Opening handshake has timed out");
        });
      }
      req.on("error", (err) => {
        if (req === null || req[kAborted]) return;
        req = websocket._req = null;
        emitErrorAndClose(websocket, err);
      });
      req.on("response", (res) => {
        const location = res.headers.location;
        const statusCode = res.statusCode;
        if (location && opts.followRedirects && statusCode >= 300 && statusCode < 400) {
          if (++websocket._redirects > opts.maxRedirects) {
            abortHandshake(websocket, req, "Maximum redirects exceeded");
            return;
          }
          req.abort();
          let addr;
          try {
            addr = new URL2(location, address2);
          } catch (e) {
            const err = new SyntaxError(`Invalid URL: ${location}`);
            emitErrorAndClose(websocket, err);
            return;
          }
          initAsClient(websocket, addr, protocols, options);
        } else if (!websocket.emit("unexpected-response", req, res)) {
          abortHandshake(
            websocket,
            req,
            `Unexpected server response: ${res.statusCode}`
          );
        }
      });
      req.on("upgrade", (res, socket, head) => {
        websocket.emit("upgrade", res);
        if (websocket.readyState !== WebSocket3.CONNECTING) return;
        req = websocket._req = null;
        const upgrade = res.headers.upgrade;
        if (upgrade === void 0 || upgrade.toLowerCase() !== "websocket") {
          abortHandshake(websocket, socket, "Invalid Upgrade header");
          return;
        }
        const digest = createHash2("sha1").update(key + GUID).digest("base64");
        if (res.headers["sec-websocket-accept"] !== digest) {
          abortHandshake(websocket, socket, "Invalid Sec-WebSocket-Accept header");
          return;
        }
        const serverProt = res.headers["sec-websocket-protocol"];
        let protError;
        if (serverProt !== void 0) {
          if (!protocolSet.size) {
            protError = "Server sent a subprotocol but none was requested";
          } else if (!protocolSet.has(serverProt)) {
            protError = "Server sent an invalid subprotocol";
          }
        } else if (protocolSet.size) {
          protError = "Server sent no subprotocol";
        }
        if (protError) {
          abortHandshake(websocket, socket, protError);
          return;
        }
        if (serverProt) websocket._protocol = serverProt;
        const secWebSocketExtensions = res.headers["sec-websocket-extensions"];
        if (secWebSocketExtensions !== void 0) {
          if (!perMessageDeflate) {
            const message = "Server sent a Sec-WebSocket-Extensions header but no extension was requested";
            abortHandshake(websocket, socket, message);
            return;
          }
          let extensions;
          try {
            extensions = parse(secWebSocketExtensions);
          } catch (err) {
            const message = "Invalid Sec-WebSocket-Extensions header";
            abortHandshake(websocket, socket, message);
            return;
          }
          const extensionNames = Object.keys(extensions);
          if (extensionNames.length !== 1 || extensionNames[0] !== PerMessageDeflate2.extensionName) {
            const message = "Server indicated an extension that was not requested";
            abortHandshake(websocket, socket, message);
            return;
          }
          try {
            perMessageDeflate.accept(extensions[PerMessageDeflate2.extensionName]);
          } catch (err) {
            const message = "Invalid Sec-WebSocket-Extensions header";
            abortHandshake(websocket, socket, message);
            return;
          }
          websocket._extensions[PerMessageDeflate2.extensionName] = perMessageDeflate;
        }
        websocket.setSocket(socket, head, {
          allowSynchronousEvents: opts.allowSynchronousEvents,
          generateMask: opts.generateMask,
          maxBufferedChunks: opts.maxBufferedChunks,
          maxFragments: opts.maxFragments,
          maxPayload: opts.maxPayload,
          skipUTF8Validation: opts.skipUTF8Validation
        });
      });
      if (opts.finishRequest) {
        opts.finishRequest(req, websocket);
      } else {
        req.end();
      }
    }
    function emitErrorAndClose(websocket, err) {
      websocket._readyState = WebSocket3.CLOSING;
      websocket._errorEmitted = true;
      websocket.emit("error", err);
      websocket.emitClose();
    }
    function netConnect(options) {
      options.path = options.socketPath;
      return net.connect(options);
    }
    function tlsConnect(options) {
      options.path = void 0;
      if (!options.servername && options.servername !== "") {
        options.servername = net.isIP(options.host) ? "" : options.host;
      }
      return tls.connect(options);
    }
    function abortHandshake(websocket, stream, message) {
      websocket._readyState = WebSocket3.CLOSING;
      const err = new Error(message);
      Error.captureStackTrace(err, abortHandshake);
      if (stream.setHeader) {
        stream[kAborted] = true;
        stream.abort();
        if (stream.socket && !stream.socket.destroyed) {
          stream.socket.destroy();
        }
        process.nextTick(emitErrorAndClose, websocket, err);
      } else {
        stream.destroy(err);
        stream.once("error", websocket.emit.bind(websocket, "error"));
        stream.once("close", websocket.emitClose.bind(websocket));
      }
    }
    function sendAfterClose(websocket, data2, cb) {
      if (data2) {
        const length = isBlob(data2) ? data2.size : toBuffer(data2).length;
        if (websocket._socket) websocket._sender._bufferedBytes += length;
        else websocket._bufferedAmount += length;
      }
      if (cb) {
        const err = new Error(
          `WebSocket is not open: readyState ${websocket.readyState} (${readyStates[websocket.readyState]})`
        );
        process.nextTick(cb, err);
      }
    }
    function receiverOnConclude(code, reason) {
      const websocket = this[kWebSocket];
      websocket._closeFrameReceived = true;
      websocket._closeMessage = reason;
      websocket._closeCode = code;
      if (websocket._socket[kWebSocket] === void 0) return;
      websocket._socket.removeListener("data", socketOnData);
      process.nextTick(resume, websocket._socket);
      if (code === 1005) websocket.close();
      else websocket.close(code, reason);
    }
    function receiverOnDrain() {
      const websocket = this[kWebSocket];
      if (!websocket.isPaused) websocket._socket.resume();
    }
    function receiverOnError(err) {
      const websocket = this[kWebSocket];
      if (websocket._socket[kWebSocket] !== void 0) {
        websocket._socket.removeListener("data", socketOnData);
        process.nextTick(resume, websocket._socket);
        websocket.close(err[kStatusCode]);
      }
      if (!websocket._errorEmitted) {
        websocket._errorEmitted = true;
        websocket.emit("error", err);
      }
    }
    function receiverOnFinish() {
      this[kWebSocket].emitClose();
    }
    function receiverOnMessage(data2, isBinary) {
      this[kWebSocket].emit("message", data2, isBinary);
    }
    function receiverOnPing(data2) {
      const websocket = this[kWebSocket];
      if (websocket._autoPong) websocket.pong(data2, !this._isServer, NOOP);
      websocket.emit("ping", data2);
    }
    function receiverOnPong(data2) {
      this[kWebSocket].emit("pong", data2);
    }
    function resume(stream) {
      stream.resume();
    }
    function senderOnError(err) {
      const websocket = this[kWebSocket];
      if (websocket.readyState === WebSocket3.CLOSED) return;
      if (websocket.readyState === WebSocket3.OPEN) {
        websocket._readyState = WebSocket3.CLOSING;
        setCloseTimer(websocket);
      }
      this._socket.end();
      if (!websocket._errorEmitted) {
        websocket._errorEmitted = true;
        websocket.emit("error", err);
      }
    }
    function setCloseTimer(websocket) {
      websocket._closeTimer = setTimeout(
        websocket._socket.destroy.bind(websocket._socket),
        websocket._closeTimeout
      );
    }
    function socketOnClose() {
      const websocket = this[kWebSocket];
      this.removeListener("close", socketOnClose);
      this.removeListener("data", socketOnData);
      this.removeListener("end", socketOnEnd);
      websocket._readyState = WebSocket3.CLOSING;
      if (!this._readableState.endEmitted && !websocket._closeFrameReceived && !websocket._receiver._writableState.errorEmitted && this._readableState.length !== 0) {
        const chunk = this.read(this._readableState.length);
        websocket._receiver.write(chunk);
      }
      websocket._receiver.end();
      this[kWebSocket] = void 0;
      clearTimeout(websocket._closeTimer);
      if (websocket._receiver._writableState.finished || websocket._receiver._writableState.errorEmitted) {
        websocket.emitClose();
      } else {
        websocket._receiver.on("error", receiverOnFinish);
        websocket._receiver.on("finish", receiverOnFinish);
      }
    }
    function socketOnData(chunk) {
      if (!this[kWebSocket]._receiver.write(chunk)) {
        this.pause();
      }
    }
    function socketOnEnd() {
      const websocket = this[kWebSocket];
      websocket._readyState = WebSocket3.CLOSING;
      websocket._receiver.end();
      this.end();
    }
    function socketOnError() {
      const websocket = this[kWebSocket];
      this.removeListener("error", socketOnError);
      this.on("error", NOOP);
      if (websocket) {
        websocket._readyState = WebSocket3.CLOSING;
        this.destroy();
      }
    }
  }
});

// ../../node_modules/ws/lib/stream.js
var require_stream = __commonJS({
  "../../node_modules/ws/lib/stream.js"(exports, module) {
    "use strict";
    var WebSocket3 = require_websocket();
    var { Duplex } = __require("stream");
    function emitClose(stream) {
      stream.emit("close");
    }
    function duplexOnEnd() {
      if (!this.destroyed && this._writableState.finished) {
        this.destroy();
      }
    }
    function duplexOnError(err) {
      this.removeListener("error", duplexOnError);
      this.destroy();
      if (this.listenerCount("error") === 0) {
        this.emit("error", err);
      }
    }
    function createWebSocketStream2(ws, options) {
      let terminateOnDestroy = true;
      const duplex = new Duplex({
        ...options,
        autoDestroy: false,
        emitClose: false,
        objectMode: false,
        writableObjectMode: false
      });
      ws.on("message", function message(msg, isBinary) {
        const data2 = !isBinary && duplex._readableState.objectMode ? msg.toString() : msg;
        if (!duplex.push(data2)) ws.pause();
      });
      ws.once("error", function error(err) {
        if (duplex.destroyed) return;
        terminateOnDestroy = false;
        duplex.destroy(err);
      });
      ws.once("close", function close() {
        if (duplex.destroyed) return;
        duplex.push(null);
      });
      duplex._destroy = function(err, callback) {
        if (ws.readyState === ws.CLOSED) {
          callback(err);
          process.nextTick(emitClose, duplex);
          return;
        }
        let called = false;
        ws.once("error", function error(err2) {
          called = true;
          callback(err2);
        });
        ws.once("close", function close() {
          if (!called) callback(err);
          process.nextTick(emitClose, duplex);
        });
        if (terminateOnDestroy) ws.terminate();
      };
      duplex._final = function(callback) {
        if (ws.readyState === ws.CONNECTING) {
          ws.once("open", function open() {
            duplex._final(callback);
          });
          return;
        }
        if (ws._socket === null) return;
        if (ws._socket._writableState.finished) {
          callback();
          if (duplex._readableState.endEmitted) duplex.destroy();
        } else {
          ws._socket.once("finish", function finish() {
            callback();
          });
          ws.close();
        }
      };
      duplex._read = function() {
        if (ws.isPaused) ws.resume();
      };
      duplex._write = function(chunk, encoding, callback) {
        if (ws.readyState === ws.CONNECTING) {
          ws.once("open", function open() {
            duplex._write(chunk, encoding, callback);
          });
          return;
        }
        ws.send(chunk, callback);
      };
      duplex.on("end", duplexOnEnd);
      duplex.on("error", duplexOnError);
      return duplex;
    }
    module.exports = createWebSocketStream2;
  }
});

// ../../node_modules/ws/lib/subprotocol.js
var require_subprotocol = __commonJS({
  "../../node_modules/ws/lib/subprotocol.js"(exports, module) {
    "use strict";
    var { tokenChars } = require_validation();
    function parse(header) {
      const protocols = /* @__PURE__ */ new Set();
      let start = -1;
      let end = -1;
      let i = 0;
      for (i; i < header.length; i++) {
        const code = header.charCodeAt(i);
        if (end === -1 && tokenChars[code] === 1) {
          if (start === -1) start = i;
        } else if (i !== 0 && (code === 32 || code === 9)) {
          if (end === -1 && start !== -1) end = i;
        } else if (code === 44) {
          if (start === -1) {
            throw new SyntaxError(`Unexpected character at index ${i}`);
          }
          if (end === -1) end = i;
          const protocol2 = header.slice(start, end);
          if (protocols.has(protocol2)) {
            throw new SyntaxError(`The "${protocol2}" subprotocol is duplicated`);
          }
          protocols.add(protocol2);
          start = end = -1;
        } else {
          throw new SyntaxError(`Unexpected character at index ${i}`);
        }
      }
      if (start === -1 || end !== -1) {
        throw new SyntaxError("Unexpected end of input");
      }
      const protocol = header.slice(start, i);
      if (protocols.has(protocol)) {
        throw new SyntaxError(`The "${protocol}" subprotocol is duplicated`);
      }
      protocols.add(protocol);
      return protocols;
    }
    module.exports = { parse };
  }
});

// ../../node_modules/ws/lib/websocket-server.js
var require_websocket_server = __commonJS({
  "../../node_modules/ws/lib/websocket-server.js"(exports, module) {
    "use strict";
    var EventEmitter = __require("events");
    var http = __require("http");
    var { Duplex } = __require("stream");
    var { createHash: createHash2 } = __require("crypto");
    var extension2 = require_extension();
    var PerMessageDeflate2 = require_permessage_deflate();
    var subprotocol2 = require_subprotocol();
    var WebSocket3 = require_websocket();
    var { CLOSE_TIMEOUT, GUID, kWebSocket } = require_constants();
    var keyRegex = /^[+/0-9A-Za-z]{22}==$/;
    var RUNNING = 0;
    var CLOSING = 1;
    var CLOSED = 2;
    var WebSocketServer2 = class extends EventEmitter {
      /**
       * Create a `WebSocketServer` instance.
       *
       * @param {Object} options Configuration options
       * @param {Boolean} [options.allowSynchronousEvents=true] Specifies whether
       *     any of the `'message'`, `'ping'`, and `'pong'` events can be emitted
       *     multiple times in the same tick
       * @param {Boolean} [options.autoPong=true] Specifies whether or not to
       *     automatically send a pong in response to a ping
       * @param {Number} [options.backlog=511] The maximum length of the queue of
       *     pending connections
       * @param {Boolean} [options.clientTracking=true] Specifies whether or not to
       *     track clients
       * @param {Number} [options.closeTimeout=30000] Duration in milliseconds to
       *     wait for the closing handshake to finish after `websocket.close()` is
       *     called
       * @param {Function} [options.handleProtocols] A hook to handle protocols
       * @param {String} [options.host] The hostname where to bind the server
       * @param {Number} [options.maxBufferedChunks=262144] The maximum number of
       *     buffered data chunks
       * @param {Number} [options.maxFragments=16384] The maximum number of message
       *     fragments
       * @param {Number} [options.maxPayload=104857600] The maximum allowed message
       *     size
       * @param {Boolean} [options.noServer=false] Enable no server mode
       * @param {String} [options.path] Accept only connections matching this path
       * @param {(Boolean|Object)} [options.perMessageDeflate=false] Enable/disable
       *     permessage-deflate
       * @param {Number} [options.port] The port where to bind the server
       * @param {(http.Server|https.Server)} [options.server] A pre-created HTTP/S
       *     server to use
       * @param {Boolean} [options.skipUTF8Validation=false] Specifies whether or
       *     not to skip UTF-8 validation for text and close messages
       * @param {Function} [options.verifyClient] A hook to reject connections
       * @param {Function} [options.WebSocket=WebSocket] Specifies the `WebSocket`
       *     class to use. It must be the `WebSocket` class or class that extends it
       * @param {Function} [callback] A listener for the `listening` event
       */
      constructor(options, callback) {
        super();
        options = {
          allowSynchronousEvents: true,
          autoPong: true,
          maxBufferedChunks: 256 * 1024,
          maxFragments: 16 * 1024,
          maxPayload: 100 * 1024 * 1024,
          skipUTF8Validation: false,
          perMessageDeflate: false,
          handleProtocols: null,
          clientTracking: true,
          closeTimeout: CLOSE_TIMEOUT,
          verifyClient: null,
          noServer: false,
          backlog: null,
          // use default (511 as implemented in net.js)
          server: null,
          host: null,
          path: null,
          port: null,
          WebSocket: WebSocket3,
          ...options
        };
        if (options.port == null && !options.server && !options.noServer || options.port != null && (options.server || options.noServer) || options.server && options.noServer) {
          throw new TypeError(
            'One and only one of the "port", "server", or "noServer" options must be specified'
          );
        }
        if (options.port != null) {
          this._server = http.createServer((req, res) => {
            const body = http.STATUS_CODES[426];
            res.writeHead(426, {
              "Content-Length": body.length,
              "Content-Type": "text/plain"
            });
            res.end(body);
          });
          this._server.listen(
            options.port,
            options.host,
            options.backlog,
            callback
          );
        } else if (options.server) {
          this._server = options.server;
        }
        if (this._server) {
          const emitConnection = this.emit.bind(this, "connection");
          this._removeListeners = addListeners(this._server, {
            listening: this.emit.bind(this, "listening"),
            error: this.emit.bind(this, "error"),
            upgrade: (req, socket, head) => {
              this.handleUpgrade(req, socket, head, emitConnection);
            }
          });
        }
        if (options.perMessageDeflate === true) options.perMessageDeflate = {};
        if (options.clientTracking) {
          this.clients = /* @__PURE__ */ new Set();
          this._shouldEmitClose = false;
        }
        this.options = options;
        this._state = RUNNING;
      }
      /**
       * Returns the bound address, the address family name, and port of the server
       * as reported by the operating system if listening on an IP socket.
       * If the server is listening on a pipe or UNIX domain socket, the name is
       * returned as a string.
       *
       * @return {(Object|String|null)} The address of the server
       * @public
       */
      address() {
        if (this.options.noServer) {
          throw new Error('The server is operating in "noServer" mode');
        }
        if (!this._server) return null;
        return this._server.address();
      }
      /**
       * Stop the server from accepting new connections and emit the `'close'` event
       * when all existing connections are closed.
       *
       * @param {Function} [cb] A one-time listener for the `'close'` event
       * @public
       */
      close(cb) {
        if (this._state === CLOSED) {
          if (cb) {
            this.once("close", () => {
              cb(new Error("The server is not running"));
            });
          }
          process.nextTick(emitClose, this);
          return;
        }
        if (cb) this.once("close", cb);
        if (this._state === CLOSING) return;
        this._state = CLOSING;
        if (this.options.noServer || this.options.server) {
          if (this._server) {
            this._removeListeners();
            this._removeListeners = this._server = null;
          }
          if (this.clients) {
            if (!this.clients.size) {
              process.nextTick(emitClose, this);
            } else {
              this._shouldEmitClose = true;
            }
          } else {
            process.nextTick(emitClose, this);
          }
        } else {
          const server = this._server;
          this._removeListeners();
          this._removeListeners = this._server = null;
          server.close(() => {
            emitClose(this);
          });
        }
      }
      /**
       * See if a given request should be handled by this server instance.
       *
       * @param {http.IncomingMessage} req Request object to inspect
       * @return {Boolean} `true` if the request is valid, else `false`
       * @public
       */
      shouldHandle(req) {
        if (this.options.path) {
          const index = req.url.indexOf("?");
          const pathname = index !== -1 ? req.url.slice(0, index) : req.url;
          if (pathname !== this.options.path) return false;
        }
        return true;
      }
      /**
       * Handle a HTTP Upgrade request.
       *
       * @param {http.IncomingMessage} req The request object
       * @param {Duplex} socket The network socket between the server and client
       * @param {Buffer} head The first packet of the upgraded stream
       * @param {Function} cb Callback
       * @public
       */
      handleUpgrade(req, socket, head, cb) {
        socket.on("error", socketOnError);
        const key = req.headers["sec-websocket-key"];
        const upgrade = req.headers.upgrade;
        const version = +req.headers["sec-websocket-version"];
        if (req.method !== "GET") {
          const message = "Invalid HTTP method";
          abortHandshakeOrEmitwsClientError(this, req, socket, 405, message);
          return;
        }
        if (upgrade === void 0 || upgrade.toLowerCase() !== "websocket") {
          const message = "Invalid Upgrade header";
          abortHandshakeOrEmitwsClientError(this, req, socket, 400, message);
          return;
        }
        if (key === void 0 || !keyRegex.test(key)) {
          const message = "Missing or invalid Sec-WebSocket-Key header";
          abortHandshakeOrEmitwsClientError(this, req, socket, 400, message);
          return;
        }
        if (version !== 13 && version !== 8) {
          const message = "Missing or invalid Sec-WebSocket-Version header";
          abortHandshakeOrEmitwsClientError(this, req, socket, 400, message, {
            "Sec-WebSocket-Version": "13, 8"
          });
          return;
        }
        if (!this.shouldHandle(req)) {
          abortHandshake(socket, 400);
          return;
        }
        const secWebSocketProtocol = req.headers["sec-websocket-protocol"];
        let protocols = /* @__PURE__ */ new Set();
        if (secWebSocketProtocol !== void 0) {
          try {
            protocols = subprotocol2.parse(secWebSocketProtocol);
          } catch (err) {
            const message = "Invalid Sec-WebSocket-Protocol header";
            abortHandshakeOrEmitwsClientError(this, req, socket, 400, message);
            return;
          }
        }
        const secWebSocketExtensions = req.headers["sec-websocket-extensions"];
        const extensions = {};
        if (this.options.perMessageDeflate && secWebSocketExtensions !== void 0) {
          const perMessageDeflate = new PerMessageDeflate2({
            ...this.options.perMessageDeflate,
            isServer: true,
            maxPayload: this.options.maxPayload
          });
          try {
            const offers = extension2.parse(secWebSocketExtensions);
            if (offers[PerMessageDeflate2.extensionName]) {
              perMessageDeflate.accept(offers[PerMessageDeflate2.extensionName]);
              extensions[PerMessageDeflate2.extensionName] = perMessageDeflate;
            }
          } catch (err) {
            const message = "Invalid or unacceptable Sec-WebSocket-Extensions header";
            abortHandshakeOrEmitwsClientError(this, req, socket, 400, message);
            return;
          }
        }
        if (this.options.verifyClient) {
          const info = {
            origin: req.headers[`${version === 8 ? "sec-websocket-origin" : "origin"}`],
            secure: !!(req.socket.authorized || req.socket.encrypted),
            req
          };
          if (this.options.verifyClient.length === 2) {
            this.options.verifyClient(info, (verified, code, message, headers) => {
              if (!verified) {
                return abortHandshake(socket, code || 401, message, headers);
              }
              this.completeUpgrade(
                extensions,
                key,
                protocols,
                req,
                socket,
                head,
                cb
              );
            });
            return;
          }
          if (!this.options.verifyClient(info)) return abortHandshake(socket, 401);
        }
        this.completeUpgrade(extensions, key, protocols, req, socket, head, cb);
      }
      /**
       * Upgrade the connection to WebSocket.
       *
       * @param {Object} extensions The accepted extensions
       * @param {String} key The value of the `Sec-WebSocket-Key` header
       * @param {Set} protocols The subprotocols
       * @param {http.IncomingMessage} req The request object
       * @param {Duplex} socket The network socket between the server and client
       * @param {Buffer} head The first packet of the upgraded stream
       * @param {Function} cb Callback
       * @throws {Error} If called more than once with the same socket
       * @private
       */
      completeUpgrade(extensions, key, protocols, req, socket, head, cb) {
        if (!socket.readable || !socket.writable) return socket.destroy();
        if (socket[kWebSocket]) {
          throw new Error(
            "server.handleUpgrade() was called more than once with the same socket, possibly due to a misconfiguration"
          );
        }
        if (this._state > RUNNING) return abortHandshake(socket, 503);
        const digest = createHash2("sha1").update(key + GUID).digest("base64");
        const headers = [
          "HTTP/1.1 101 Switching Protocols",
          "Upgrade: websocket",
          "Connection: Upgrade",
          `Sec-WebSocket-Accept: ${digest}`
        ];
        const ws = new this.options.WebSocket(null, void 0, this.options);
        if (protocols.size) {
          const protocol = this.options.handleProtocols ? this.options.handleProtocols(protocols, req) : protocols.values().next().value;
          if (protocol) {
            headers.push(`Sec-WebSocket-Protocol: ${protocol}`);
            ws._protocol = protocol;
          }
        }
        if (extensions[PerMessageDeflate2.extensionName]) {
          const params = extensions[PerMessageDeflate2.extensionName].params;
          const value = extension2.format({
            [PerMessageDeflate2.extensionName]: [params]
          });
          headers.push(`Sec-WebSocket-Extensions: ${value}`);
          ws._extensions = extensions;
        }
        this.emit("headers", headers, req);
        socket.write(headers.concat("\r\n").join("\r\n"));
        socket.removeListener("error", socketOnError);
        ws.setSocket(socket, head, {
          allowSynchronousEvents: this.options.allowSynchronousEvents,
          maxBufferedChunks: this.options.maxBufferedChunks,
          maxFragments: this.options.maxFragments,
          maxPayload: this.options.maxPayload,
          skipUTF8Validation: this.options.skipUTF8Validation
        });
        if (this.clients) {
          this.clients.add(ws);
          ws.on("close", () => {
            this.clients.delete(ws);
            if (this._shouldEmitClose && !this.clients.size) {
              process.nextTick(emitClose, this);
            }
          });
        }
        cb(ws, req);
      }
    };
    module.exports = WebSocketServer2;
    function addListeners(server, map) {
      for (const event of Object.keys(map)) server.on(event, map[event]);
      return function removeListeners() {
        for (const event of Object.keys(map)) {
          server.removeListener(event, map[event]);
        }
      };
    }
    function emitClose(server) {
      server._state = CLOSED;
      server.emit("close");
    }
    function socketOnError() {
      this.destroy();
    }
    function abortHandshake(socket, code, message, headers) {
      message = message || http.STATUS_CODES[code];
      headers = {
        Connection: "close",
        "Content-Type": "text/html",
        "Content-Length": Buffer.byteLength(message),
        ...headers
      };
      socket.once("finish", socket.destroy);
      socket.end(
        `HTTP/1.1 ${code} ${http.STATUS_CODES[code]}\r
` + Object.keys(headers).map((h) => `${h}: ${headers[h]}`).join("\r\n") + "\r\n\r\n" + message
      );
    }
    function abortHandshakeOrEmitwsClientError(server, req, socket, code, message, headers) {
      if (server.listenerCount("wsClientError")) {
        const err = new Error(message);
        Error.captureStackTrace(err, abortHandshakeOrEmitwsClientError);
        server.emit("wsClientError", err, socket, req);
      } else {
        abortHandshake(socket, code, message, headers);
      }
    }
  }
});

// box/main.ts
import { readFileSync as readFileSync3 } from "node:fs";
import { createInterface } from "node:readline";

// ../../packages/core/src/validate/read.ts
var Invalid = class extends Error {
};
var BASE64 = /^[A-Za-z0-9+/]*={0,2}$/;
var UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
var CONTROL = /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f]/;
function parseJson(raw, at) {
  try {
    const value = JSON.parse(raw);
    return value;
  } catch {
    throw new Invalid(`${at}: not JSON`);
  }
}
function maybeRecord(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? new Map(Object.entries(value)) : void 0;
}
function record(value, at) {
  const fields2 = maybeRecord(value);
  if (fields2 === void 0) throw new Invalid(`${at}: not an object`);
  return fields2;
}
function fields(value, at, required, optional = []) {
  const f = record(value, at);
  for (const key of f.keys()) if (!required.includes(key) && !optional.includes(key)) throw new Invalid(`${at}.${key}: not expected`);
  for (const key of required) if (f.get(key) === void 0) throw new Invalid(`${at}.${key}: missing`);
  return f;
}
function list(value, at) {
  if (!Array.isArray(value)) throw new Invalid(`${at}: not a list`);
  return value.map((item) => item);
}
function text(value, at, o) {
  if (typeof value !== "string") throw new Invalid(`${at}: not text`);
  if (value.length < (o.min ?? 1) || value.length > o.max) throw new Invalid(`${at}: ${o.min ?? 1} to ${o.max} characters`);
  if (CONTROL.test(value)) throw new Invalid(`${at}: holds a control character`);
  if (o.pattern !== void 0 && !o.pattern.test(value)) throw new Invalid(`${at}: not in the expected form`);
  return value;
}
var AHEAD_MS = 5 * 6e4;
function integer(value, at) {
  if (typeof value !== "number" || !Number.isSafeInteger(value)) throw new Invalid(`${at}: not a whole number`);
  return value;
}
function bool(value, at) {
  if (typeof value !== "boolean") throw new Invalid(`${at}: not true or false`);
  return value;
}
function number(value, at, o) {
  if (typeof value !== "number" || !Number.isFinite(value) || value < o.min || value > o.max) throw new Invalid(`${at}: a number from ${o.min} to ${o.max}`);
  return value;
}
function isOneOf(value, values) {
  return values.some((v) => v === value);
}
function oneOf(value, at, values) {
  if (typeof value !== "string" || !isOneOf(value, values)) throw new Invalid(`${at}: one of ${values.join(", ")}`);
  return value;
}
function uuid(value, at) {
  return text(value, at, { max: 36, pattern: UUID });
}
function nullable(value, read) {
  return value === void 0 || value === null ? null : read(value);
}

// box/program.ts
import { createPrivateKey, createPublicKey, randomUUID, sign, X509Certificate } from "node:crypto";
import { readFileSync as readFileSync2 } from "node:fs";
import { networkInterfaces } from "node:os";
import { createSecureContext } from "node:tls";

// ../../packages/core/src/der.ts
function tlv(tag, content) {
  const n = content.length;
  const length = n < 128 ? Buffer.from([n]) : (() => {
    const bytes = [];
    for (let x = n; x > 0; x >>= 8) bytes.unshift(x & 255);
    return Buffer.from([128 | bytes.length, ...bytes]);
  })();
  return Buffer.concat([Buffer.from([tag]), length, content]);
}
function oidContent(dotted) {
  const [a = 0, b = 0, ...rest] = dotted.split(".").map(Number);
  const bytes = [40 * a + b];
  for (const n of rest) {
    const sevens = [];
    for (let x = n; ; x = Math.floor(x / 128)) {
      sevens.unshift(x % 128 | (sevens.length === 0 ? 0 : 128));
      if (x < 128) break;
    }
    bytes.push(...sevens);
  }
  return Buffer.from(bytes);
}
var oid = (dotted) => tlv(6, oidContent(dotted));

// ../../packages/core/src/csr.ts
var sequence = (...parts2) => tlv(48, Buffer.concat(parts2));
var set = (...parts2) => tlv(49, Buffer.concat(parts2));
var COMMON_NAME = "2.5.4.3";
var EXTENSION_REQUEST = "1.2.840.113549.1.9.14";
var SUBJECT_ALT_NAME = "2.5.29.17";
var DNS_NAME = /^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$/;
function csrInfo(name, spki) {
  if (!DNS_NAME.test(name)) throw new Error(`${name}: not a DNS name`);
  const subject = sequence(set(sequence(oid(COMMON_NAME), tlv(12, Buffer.from(name)))));
  const altNames = sequence(tlv(130, Buffer.from(name)));
  const extensions = sequence(sequence(oid(SUBJECT_ALT_NAME), tlv(4, altNames)));
  const attributes = tlv(160, sequence(oid(EXTENSION_REQUEST), set(extensions)));
  return sequence(tlv(2, Buffer.from([0])), subject, spki, attributes);
}

// shared/journal.ts
var JOURNAL_SIGNATURE = /^[0-9a-f]{64}$/;

// shared/phoneCode.ts
var DAMM = [
  "0317598642",
  "7092154863",
  "4206871359",
  "1750983426",
  "6123045978",
  "3674209581",
  "5869720134",
  "8945362017",
  "9438617205",
  "2581436790"
].map((row) => [...row].map(Number));

// shared/scan.ts
var GIFT_CARD_CODE_MAX = 64;
var SCAN_CODE = /^[\x20-\x7e]{1,64}$/;

// shared/box.ts
var MESSAGE_MAX = 65536;
var DEVICES_MAX = 16;
var FOUND_MAX = 64;
var BLOCKS_MAX = 500;
var FEED_MAX = 10;
var DOTS_MAX = 576;
var DOTS_PER_COLUMN = 12;
var LOGO_BYTES_MAX = 24576;
var PEM_MAX = 16384;
var KEY = /^[A-Za-z0-9_-]{43}$/;
var VERSION = /^\d{1,4}\.\d{1,4}\.\d{1,4}$/;
var KEY_HASH = /^[0-9a-f]{64}$/;
var HOST = /^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$/;
var CERTIFICATES = /^(?:-----BEGIN CERTIFICATE-----\n[A-Za-z0-9+/=\n]+-----END CERTIFICATE-----\n*){1,4}$/;
var SHOP_ADDRESS = /^(?:10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])|192\.168)\.\d{1,3}\.\d{1,3}$/;
var BASE64_OF = (max) => ({ max, pattern: /^[A-Za-z0-9+/]+={0,2}$/ });
var INSTANT = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/;
var SCANNER_STATES = ["connected", "disconnected"];
var PRINTER_STATES = ["ready", "paper_low", "paper_out", "cover_open", "offline"];
var FAILURES = ["paper_out", "cover_open", "offline", "error"];
var SUFFIXES = ["none", "auto", "cr", "lf", "crlf", "tab"];
var clock = (v, at) => integer(number(v, at, { min: 0, max: 864e13 }), at);
var signedOf = (f) => ({
  body: record(f.get("body"), "body"),
  at: clock(f.get("at"), "at"),
  window: nullable(f.get("window"), (x) => fields(x, "window", ["id", "membership", "startedAt", "endedAt", "endedBy"])),
  sig: text(f.get("sig"), "sig", { max: 64, pattern: JOURNAL_SIGNATURE })
});
function typed(raw, types) {
  if (raw.length > MESSAGE_MAX) throw new Invalid(`message: over ${String(MESSAGE_MAX)} characters`);
  const v = parseJson(raw, "message");
  return { v, type: oneOf(record(v, "message").get("type"), "message.type", types) };
}
function instant(v, at) {
  const s = text(v, at, { max: 24, pattern: INSTANT });
  if (new Date(s).toISOString() !== s) throw new Invalid(`${at}: not a moment`);
  return s;
}
var scanOf = (f) => ({ id: uuid(f.get("id"), "id"), code: text(f.get("code"), "code", { max: 64, pattern: SCAN_CODE }), at: instant(f.get("at"), "at") });
function devices(v, at, states) {
  const all = list(v, at);
  if (all.length > DEVICES_MAX) throw new Invalid(`${at}: more than ${String(DEVICES_MAX)}`);
  return all.map((d, i) => {
    const f = fields(d, `${at}[${String(i)}]`, ["id", "state"]);
    return { id: uuid(f.get("id"), "id"), state: oneOf(f.get("state"), "state", states) };
  });
}
function fromBox(raw) {
  const { v, type } = typed(raw, ["hello", "scan", "status", "job", "journal", "csr", "found"]);
  switch (type) {
    case "hello": {
      const f = fields(v, "hello", ["type", "key", "version"]);
      return { type, key: text(f.get("key"), "key", { max: 43, pattern: KEY }), version: text(f.get("version"), "version", { max: 14, pattern: VERSION }) };
    }
    case "scan": {
      const f = fields(v, "scan", ["type", "id", "scanner", "code", "at"]);
      return { type, scanner: uuid(f.get("scanner"), "scanner"), ...scanOf(f) };
    }
    case "status": {
      const f = fields(v, "status", ["type", "scanners", "printers", "internet"]);
      return { type, scanners: devices(f.get("scanners"), "scanners", SCANNER_STATES), printers: devices(f.get("printers"), "printers", PRINTER_STATES), internet: bool(f.get("internet"), "internet") };
    }
    case "job": {
      const state = oneOf(record(v, "job").get("state"), "state", ["printed", "failed", "uncertain"]);
      if (state !== "failed") return { type, job: uuid(fields(v, "job", ["type", "job", "state"]).get("job"), "job"), state };
      const f = fields(v, "job", ["type", "job", "state", "reason"]);
      return { type, job: uuid(f.get("job"), "job"), state, reason: oneOf(f.get("reason"), "reason", FAILURES) };
    }
    case "csr": {
      const f = fields(v, "csr", ["type", "address", "key", "signature"]);
      return {
        type,
        address: text(f.get("address"), "address", { max: 15, pattern: SHOP_ADDRESS }),
        key: text(f.get("key"), "key", BASE64_OF(400)),
        signature: text(f.get("signature"), "signature", BASE64_OF(200))
      };
    }
    case "found": {
      const f = fields(v, "found", ["type", "scanners", "printers"]);
      return { type, scanners: foundOf(f.get("scanners"), "scanners"), printers: foundOf(f.get("printers"), "printers") };
    }
    case "journal": {
      const f = fields(v, "journal", ["type", "sale", "register", "body", "at", "window", "sig", "receivedAt", "sentAt"]);
      return {
        type,
        sale: uuid(f.get("sale"), "sale"),
        register: uuid(f.get("register"), "register"),
        ...signedOf(f),
        receivedAt: clock(f.get("receivedAt"), "receivedAt"),
        sentAt: clock(f.get("sentAt"), "sentAt")
      };
    }
  }
}
var words = (v, at, max = 200) => text(v, at, { max, min: 0 });
function only(v, at, value) {
  if (v !== value) throw new Invalid(`${at}: only ${String(value)}`);
  return value;
}
function blockOf(v, at) {
  const t = oneOf(record(v, at).get("t"), `${at}.t`, ["logo", "text", "rule", "line", "barcode", "feed", "cut"]);
  switch (t) {
    case "logo":
      return { t, text: words(fields(v, at, ["t", "text"]).get("text"), `${at}.text`) };
    case "text": {
      const f = fields(v, at, ["t", "text", "align"], ["bold", "size"]);
      const bold = f.get("bold") === void 0 ? {} : { bold: only(f.get("bold"), `${at}.bold`, true) };
      const size = f.get("size") === void 0 ? {} : { size: only(f.get("size"), `${at}.size`, "double") };
      return { t, text: words(f.get("text"), `${at}.text`, 1e3), align: oneOf(f.get("align"), `${at}.align`, ["left", "center"]), ...bold, ...size };
    }
    case "line": {
      const f = fields(v, at, ["t", "left", "right"], ["sub", "bold"]);
      const sub = f.get("sub") === void 0 ? {} : { sub: words(f.get("sub"), `${at}.sub`) };
      const bold = f.get("bold") === void 0 ? {} : { bold: only(f.get("bold"), `${at}.bold`, true) };
      return { t, left: words(f.get("left"), `${at}.left`), right: words(f.get("right"), `${at}.right`), ...sub, ...bold };
    }
    case "barcode":
      return { t, data: text(fields(v, at, ["t", "data"]).get("data"), `${at}.data`, { max: 64, pattern: SCAN_CODE }) };
    case "feed": {
      const lines2 = integer(fields(v, at, ["t", "lines"]).get("lines"), `${at}.lines`);
      if (lines2 < 1 || lines2 > FEED_MAX) throw new Invalid(`${at}.lines: 1 to ${String(FEED_MAX)}`);
      return { t, lines: lines2 };
    }
    case "rule":
    case "cut":
      fields(v, at, ["t"]);
      return { t };
  }
}
function docOf(v) {
  const f = fields(v, "doc", ["width", "blocks"]);
  const blocks = list(f.get("blocks"), "doc.blocks");
  if (blocks.length > BLOCKS_MAX) throw new Invalid(`doc.blocks: more than ${String(BLOCKS_MAX)}`);
  return { width: only(f.get("width"), "doc.width", "auto"), blocks: blocks.map((b, i) => blockOf(b, `doc.blocks[${String(i)}]`)) };
}
function each(v, at, keys, read) {
  const all = list(v, at);
  if (all.length > DEVICES_MAX) throw new Invalid(`${at}: more than ${String(DEVICES_MAX)}`);
  const read_ = all.map((d, i) => read(fields(d, `${at}[${String(i)}]`, keys)));
  if (new Set(read_.map((d) => d.id)).size !== read_.length) throw new Invalid(`${at}: an id twice`);
  return read_;
}
function between(v, at, min, max) {
  const n = integer(v, at);
  if (n < min || n > max) throw new Invalid(`${at}: ${String(min)} to ${String(max)}`);
  return n;
}
function drawerPin(v) {
  if (v !== 2 && v !== 5) throw new Invalid("drawerPin: 2 or 5");
  return v;
}
var address = (f) => ({ host: text(f.get("host"), "host", { max: 253, pattern: HOST }), port: between(f.get("port"), "port", 1, 65535) });
function foundOf(v, at) {
  const all = list(v, at);
  if (all.length > FOUND_MAX) throw new Invalid(`${at}: more than ${String(FOUND_MAX)}`);
  return all.map((d, i) => {
    const f = fields(d, `${at}[${String(i)}]`, ["host", "port"], ["name"]);
    return { ...address(f), name: f.get("name") === void 0 ? null : nullable(f.get("name"), (n) => text(n, "name", { max: 64, pattern: /^[\x20-\x7e]+$/ })) };
  });
}
function logoOf(v) {
  const f = fields(v, "logo", ["width", "height", "bits"]);
  const width = between(f.get("width"), "logo.width", 8, DOTS_MAX);
  if (width % 8 !== 0) throw new Invalid("logo.width: whole bytes");
  const bytes = width / 8 * between(f.get("height"), "logo.height", 1, LOGO_BYTES_MAX);
  if (bytes > LOGO_BYTES_MAX) throw new Invalid(`logo: over ${String(LOGO_BYTES_MAX)} bytes`);
  const bits = text(f.get("bits"), "logo.bits", { max: 4 * Math.ceil(LOGO_BYTES_MAX / 3), pattern: BASE64 });
  if (bits.length !== 4 * Math.ceil(bytes / 3) || bits.split("=").length - 1 !== (3 - bytes % 3) % 3) throw new Invalid("logo.bits: not width / 8 by height bytes");
  return { width, height: bytes / (width / 8), bits };
}
function settingsOf(v) {
  const f = fields(v, "settings", ["type", "scanners", "printers", "registers", "logo", "name"]);
  return {
    scanners: each(f.get("scanners"), "scanners", ["id", "register", "host", "port", "suffix"], (s) => ({
      id: uuid(s.get("id"), "id"),
      register: uuid(s.get("register"), "register"),
      ...address(s),
      suffix: oneOf(s.get("suffix"), "suffix", SUFFIXES)
    })),
    printers: each(f.get("printers"), "printers", ["id", "host", "port", "columns", "drawerPin"], (p) => ({
      id: uuid(p.get("id"), "id"),
      ...address(p),
      columns: between(p.get("columns"), "columns", 32, 48),
      drawerPin: drawerPin(p.get("drawerPin"))
    })),
    registers: each(f.get("registers"), "registers", ["id", "register", "keyHash", "printer"], (r) => ({
      id: uuid(r.get("id"), "id"),
      register: uuid(r.get("register"), "register"),
      keyHash: text(r.get("keyHash"), "keyHash", { max: 64, pattern: KEY_HASH }),
      printer: nullable(r.get("printer"), (p) => uuid(p, "printer"))
    })),
    logo: nullable(f.get("logo"), logoOf),
    name: nullable(f.get("name"), (n) => text(n, "name", { max: 253, pattern: HOST }))
  };
}
function toBox(raw) {
  const { v, type } = typed(raw, ["hello", "print", "drawer", "ack", "journal", "settings", "certificate"]);
  switch (type) {
    case "hello": {
      const f = fields(v, "hello", ["type", "register", "key"]);
      return { type, register: uuid(f.get("register"), "register"), key: text(f.get("key"), "key", { max: 43, pattern: KEY }) };
    }
    case "print": {
      const f = fields(v, "print", ["type", "job", "printer", "doc"]);
      return { type, job: uuid(f.get("job"), "job"), printer: uuid(f.get("printer"), "printer"), doc: docOf(f.get("doc")) };
    }
    case "drawer": {
      const f = fields(v, "drawer", ["type", "job", "printer"]);
      return { type, job: uuid(f.get("job"), "job"), printer: uuid(f.get("printer"), "printer") };
    }
    case "ack": {
      const f = fields(v, "ack", ["type", "of", "id"]);
      return { type, of: oneOf(f.get("of"), "of", ["scan", "sale"]), id: uuid(f.get("id"), "id") };
    }
    case "journal": {
      const f = fields(v, "journal", ["type", "sale", "body", "at", "window", "sig"]);
      return { type, sale: uuid(f.get("sale"), "sale"), ...signedOf(f) };
    }
    case "settings":
      return { type, ...settingsOf(v) };
    case "certificate": {
      const f = fields(v, "certificate", ["type", "cert"]);
      return { type, cert: text(f.get("cert"), "cert", { max: PEM_MAX, pattern: CERTIFICATES }) };
    }
  }
}

// shared/paths.ts
var PATHS = {
  sale: "/pos/",
  charge: "/pos/charge",
  done: "/pos/done",
  customer: "/pos/customer",
  held: "/pos/held",
  orders: "/pos/orders",
  order: "/pos/order",
  returns: "/pos/return",
  giftCard: "/pos/gift-card",
  drawer: "/pos/drawer",
  status: "/pos/status",
  pay: "/pos/pay",
  payOrder: "/pos/pay/order",
  receipt: "/pos/r",
  scanner: "/pos/scanner",
  office: "/pos/office",
  more: "/pos/office/more",
  reports: "/pos/office/reports",
  report: "/pos/office/report",
  officeOrders: "/pos/office/orders",
  officeOrder: "/pos/office/order",
  items: "/pos/office/items",
  item: "/pos/office/item",
  group: "/pos/office/group",
  templates: "/pos/office/templates",
  template: "/pos/office/template",
  unknownCodes: "/pos/office/unknown-codes",
  categories: "/pos/office/categories",
  customers: "/pos/office/customers",
  account: "/pos/office/customer",
  customerSettings: "/pos/office/customer-settings",
  promos: "/pos/office/promos",
  giftCards: "/pos/office/gift-cards",
  officeGiftCard: "/pos/office/gift-card",
  cash: "/pos/office/cash",
  attention: "/pos/office/attention",
  registers: "/pos/office/registers",
  scanners: "/pos/office/scanners",
  shopSettings: "/pos/office/shop",
  settings: "/pos/office/settings",
  posSettings: "/pos/office/pos-settings",
  taxRates: "/pos/office/tax-rates",
  stripe: "/pos/office/stripe",
  dispute: "/pos/office/dispute",
  look: "/pos/office/look",
  services: "/pos/office/services",
  permissions: "/pos/office/permissions"
};
var INSTALL = { manifest: "/pos/manifest.webmanifest", icons: "/pos/icons/", worker: "/pos/sw.js", scope: PATHS.sale, built: "/assets/pos" };
var PHONE_BUILD = { folder: "phone", built: `${INSTALL.built}/phone` };
var RECEIPT_BUILD = { folder: "receipt", built: `${INSTALL.built}/receipt` };
var SCANNER_BUILD = { folder: "scanner", built: `${INSTALL.built}/scanner`, manifest: "/pos/scanner/manifest.webmanifest" };
var SOCKETS = { realtime: "/api/pos/ws", box: "/api/pos/box" };

// box/found.ts
import { connect } from "node:net";

// box/snmp.ts
import { createSocket } from "node:dgram";
var PORT = 161;
var COMMUNITY = "public";
var NAME_OIDS = ["1.3.6.1.2.1.25.3.2.1.3.1", "1.3.6.1.2.1.1.1.0", "1.3.6.1.2.1.1.5.0"];
var NAME_MAX = 64;
var TAG = { integer: 2, octets: 4, nul: 5, oid: 6, sequence: 48, get: 160, response: 162 };
function integer2(n) {
  const bytes = [n & 255];
  for (let v = n >>> 8; v > 0; v >>>= 8) bytes.unshift(v & 255);
  if ((bytes[0] ?? 0) & 128) bytes.unshift(0);
  return tlv(TAG.integer, Buffer.from(bytes));
}
function getRequest(id, oid2) {
  const binding = tlv(TAG.sequence, Buffer.concat([oid(oid2), tlv(TAG.nul, Buffer.alloc(0))]));
  const pdu = tlv(TAG.get, Buffer.concat([integer2(id), integer2(0), integer2(0), tlv(TAG.sequence, binding)]));
  return tlv(TAG.sequence, Buffer.concat([integer2(0), tlv(TAG.octets, Buffer.from(COMMUNITY)), pdu]));
}
function part(b, at) {
  const tag = b[at];
  let len = b[at + 1];
  let start = at + 2;
  if (tag === void 0 || len === void 0) throw new RangeError("short");
  if (len & 128) {
    const bytes = len & 127;
    if (bytes < 1 || bytes > 2) throw new RangeError("length");
    len = 0;
    for (let i = 0; i < bytes; i++) len = len << 8 | (b[start + i] ?? 0);
    start += bytes;
  }
  if (start + len > b.length) throw new RangeError("past the end");
  return { tag, body: b.subarray(start, start + len), next: start + len };
}
function parts(b) {
  const all = [];
  for (let at = 0; at < b.length; ) {
    const p = part(b, at);
    all.push(p);
    at = p.next;
  }
  return all;
}
var intOf = (p) => p?.tag === TAG.integer && p.body.length <= 4 ? p.body.reduce((n, x) => n << 8 | x, 0) : void 0;
function responseText(b, id, oid2) {
  try {
    const [message] = parts(b);
    if (message?.tag !== TAG.sequence) return void 0;
    const [, , pdu] = parts(message.body);
    if (pdu?.tag !== TAG.response) return void 0;
    const [reqId, status, , bindings] = parts(pdu.body);
    if (intOf(reqId) !== id) return void 0;
    if (intOf(status) !== 0 || bindings?.tag !== TAG.sequence) return { text: void 0 };
    const [binding] = parts(bindings.body);
    const [name, value] = binding?.tag === TAG.sequence ? parts(binding.body) : [];
    return { text: name?.tag === TAG.oid && name.body.equals(oidContent(oid2)) && value?.tag === TAG.octets ? value.body.toString("latin1") : void 0 };
  } catch {
    return void 0;
  }
}
function nameOf(text3) {
  const name = (text3 ?? "").replace(/[^\x20-\x7e]/g, " ").replace(/ +/g, " ").trim().slice(0, NAME_MAX).trim();
  return name === "" ? null : name;
}
function snmpText(host, oid2, ms) {
  return new Promise((resolve) => {
    const id = Math.floor(Math.random() * 2147483647);
    const s = createSocket("udp4");
    const done = (text3) => {
      clearTimeout(timer);
      s.close();
      resolve(text3);
    };
    const timer = setTimeout(() => done(void 0), ms);
    s.on("error", () => done(void 0));
    s.on("message", (msg, from) => {
      if (from.address !== host || from.port !== PORT) return;
      const answer = responseText(msg, id, oid2);
      if (answer !== void 0) done(answer.text);
    });
    s.send(getRequest(id, oid2), PORT, host);
  });
}
async function deviceName(host, ms, ask = snmpText) {
  for (const oid2 of NAME_OIDS) {
    const name = nameOf(await ask(host, oid2, ms));
    if (name !== null) return name;
  }
  return null;
}

// box/found.ts
var PORTS = { scanner: 10001, printer: 9100 };
function answers(host, port, ms) {
  return new Promise((resolve) => {
    const s = connect({ host, port });
    const done = (yes) => {
      s.destroy();
      resolve(yes);
    };
    s.setTimeout(ms, () => done(false));
    s.once("connect", () => done(true));
    s.once("error", () => done(false));
  });
}
function neighbours(own) {
  const parts2 = own.split(".");
  const prefix = parts2.slice(0, 3).join(".");
  return Array.from({ length: 254 }, (_, i) => `${prefix}.${String(i + 1)}`).filter((a) => a !== own);
}
async function findOnNetwork(own, known, t, ask = answers, named = deviceName) {
  const all = neighbours(own).flatMap((host) => [{ host, port: PORTS.scanner }, { host, port: PORTS.printer }]).filter((a) => !known.has(`${a.host}:${String(a.port)}`));
  const found = [];
  let next = 0;
  const worker = async () => {
    while (next < all.length) {
      const a = all[next++];
      if (a !== void 0 && await ask(a.host, a.port, t.answerMs)) found.push(a);
    }
  };
  await Promise.all(Array.from({ length: t.atOnce }, worker));
  const order = (x, y) => Number(x.host.split(".")[3]) - Number(y.host.split(".")[3]);
  const of = (port) => found.filter((a) => a.port === port).sort(order);
  const printers = [];
  for (const p of of(PORTS.printer)) printers.push({ ...p, name: await named(p.host, 2 * t.answerMs) });
  return { scanners: of(PORTS.scanner).map((s) => ({ ...s, name: null })), printers };
}

// box/link.ts
var Link = class {
  #o;
  #peer;
  #generation = 0;
  #stopped = false;
  #retry;
  #wait;
  constructor(o) {
    this.#o = o;
    this.#wait = o.timing.firstMs;
    this.#attempt();
  }
  isOpen() {
    return this.#peer !== void 0;
  }
  send(raw) {
    this.#peer?.send(raw);
  }
  // Let the socket go now and open another as the backoff allows
  drop() {
    const p = this.#peer;
    if (p === void 0) return;
    this.#lost(this.#generation, void 0);
    p.close();
  }
  // Open another at once, with the key just exchanged
  restart() {
    const p = this.#peer;
    this.#peer = void 0;
    this.#generation++;
    clearTimeout(this.#retry);
    this.#wait = this.#o.timing.firstMs;
    if (p !== void 0) {
      this.#o.closed();
      p.close();
    }
    this.#attempt();
  }
  stop() {
    this.#stopped = true;
    clearTimeout(this.#retry);
    this.#generation++;
    this.#peer?.close();
    this.#peer = void 0;
  }
  #later(upAt) {
    if (this.#stopped) return;
    if (upAt !== void 0 && Date.now() - upAt >= this.#o.timing.steadyMs) this.#wait = this.#o.timing.firstMs;
    this.#retry = setTimeout(() => this.#attempt(), this.#wait);
    this.#wait = Math.min(this.#wait * 2, this.#o.timing.maxMs);
  }
  #lost(mine, upAt) {
    if (mine !== this.#generation) return;
    this.#generation++;
    this.#peer = void 0;
    this.#o.closed();
    this.#later(upAt);
  }
  #attempt() {
    this.#retry = void 0;
    const hello = this.#o.hello();
    if (this.#stopped || hello === void 0) return;
    const mine = ++this.#generation;
    this.#o.dial(this.#o.url, {
      opened: (p) => {
        const upAt = Date.now();
        if (mine !== this.#generation) p.close();
        else {
          this.#peer = p;
          p.send(hello);
          this.#o.opened();
        }
        return {
          message: (raw) => {
            if (mine === this.#generation) this.#o.message(raw);
          },
          closed: () => this.#lost(mine, upAt)
        };
      },
      failed: () => {
        if (mine === this.#generation) this.#later(void 0);
      }
    });
  }
};

// box/local.ts
import { createHash, timingSafeEqual } from "node:crypto";
import { createServer } from "node:https";

// box/log.ts
function log(event, detail = {}) {
  process.stdout.write(`${JSON.stringify({ at: (/* @__PURE__ */ new Date()).toISOString(), event, ...detail })}
`);
}

// box/local.ts
var PATH = "/";
var SOCKETS_MAX = 32;
var keyHashOf = (key) => createHash("sha256").update(key).digest("hex");
function sameHash(a, b) {
  return a.length === b.length && timingSafeEqual(Buffer.from(a), Buffer.from(b));
}
function admitted(raw, o) {
  const m = toBox(raw);
  if (m.type !== "hello") return void 0;
  const expected = o.keyHash(m.register);
  return expected !== void 0 && sameHash(keyHashOf(m.key), expected) ? { id: m.register, keyHash: expected } : void 0;
}
function serve(o, open, peer) {
  let register;
  const late = setTimeout(() => peer.close(), o.helloMs);
  return {
    message: (raw) => {
      try {
        if (register === void 0) {
          clearTimeout(late);
          const who = admitted(raw, o);
          if (who === void 0) throw new Error("not admitted");
          register = { ...who, send: (r) => peer.send(r), close: () => peer.close() };
          open.add(register);
          o.joined(register);
          return;
        }
        const m = toBox(raw);
        if (m.type === "hello" || m.type === "settings" || m.type === "certificate") throw new Error(`${m.type} from a register`);
        o.message(register, m);
      } catch (err) {
        log("register_refused", { error: err instanceof Error ? err.message : String(err), ...register === void 0 ? {} : { register: register.id } });
        peer.close();
      }
    },
    closed: () => {
      clearTimeout(late);
      if (register !== void 0) open.delete(register);
    }
  };
}
function refusal(req, origin, sockets) {
  if (req.url !== PATH) return "404 Not Found";
  if (req.headers.origin !== origin) return "403 Forbidden";
  return sockets >= SOCKETS_MAX ? "503 Service Unavailable" : void 0;
}
function serveLocal(o) {
  const open = /* @__PURE__ */ new Set();
  let opening = 0;
  const server = createServer({ cert: o.certificate.cert, key: o.certificate.key }, (_req, res) => {
    res.writeHead(404).end();
  });
  server.on("upgrade", (req, socket, head) => {
    const refused = refusal(req, o.origin, open.size + opening);
    if (refused !== void 0) {
      socket.end(`HTTP/1.1 ${refused}\r
Connection: close\r
Content-Length: 0\r
\r
`);
      return;
    }
    socket.on("error", () => socket.destroy());
    let counted = true;
    const settled = () => {
      if (counted) opening--;
      counted = false;
    };
    opening++;
    socket.once("close", settled);
    o.upgrade(req, socket, head, MESSAGE_MAX, (peer) => {
      settled();
      return serve(o, open, peer);
    });
  });
  server.on("tlsClientError", () => void 0);
  return new Promise((done, fail) => {
    server.once("error", fail);
    server.listen(o.port, () => {
      done({
        port: () => {
          const a = server.address();
          return typeof a === "object" && a !== null ? a.port : o.port;
        },
        registers: () => [...open],
        setCertificate: (c) => server.setSecureContext({ cert: c.cert, key: c.key }),
        close: () => new Promise((closed) => {
          for (const r of open) r.close();
          server.close(() => closed());
          server.closeAllConnections();
        })
      });
    });
  });
}

// box/pending.ts
var Pending = class {
  #items = /* @__PURE__ */ new Map();
  #o;
  constructor(o) {
    this.#o = o;
  }
  add(id, raw) {
    if (this.#items.has(id)) return;
    this.#items.set(id, { raw, sentAt: void 0, sends: 0 });
    const [oldest] = this.#items.keys();
    if (this.#items.size > this.#o.max && oldest !== void 0) this.#items.delete(oldest);
  }
  ack(id) {
    return this.#items.delete(id);
  }
  // The connection they went on is gone: the ids of those given up on
  unsent() {
    const given = [];
    for (const [id, item] of this.#items) {
      if (item.sentAt !== void 0 && item.sends >= this.#o.sends) given.push(id);
      item.sentAt = void 0;
    }
    for (const id of given) this.#items.delete(id);
    return given;
  }
  // What to send now, marked sent
  next(now) {
    const items = [...this.#items.values()];
    const room = this.#o.window - items.filter((i) => i.sentAt !== void 0).length;
    const out = items.filter((i) => i.sentAt === void 0).slice(0, Math.max(0, room));
    for (const item of out) {
      item.sentAt = now;
      item.sends++;
    }
    return out.map((i) => i.raw);
  }
  overdue(now, ms) {
    return [...this.#items.values()].some((i) => i.sentAt !== void 0 && now - i.sentAt > ms);
  }
};

// ../../packages/screens/settings/shopWords.ts
var ZONE_NAMES = /* @__PURE__ */ new Map([
  ["America/New_York", "Eastern"],
  ["America/Chicago", "Central"],
  ["America/Denver", "Mountain"],
  ["America/Phoenix", "Arizona"],
  ["America/Los_Angeles", "Pacific"],
  ["America/Anchorage", "Alaska"],
  ["Pacific/Honolulu", "Hawaii"],
  ["America/Detroit", "Eastern"],
  ["America/Indiana/Indianapolis", "Eastern"],
  ["America/Boise", "Mountain"],
  ["America/Puerto_Rico", "Atlantic"]
]);
var SHOP_WORDS = {
  addShop: "Add shop",
  noShops: "No shops",
  address: "Address",
  city: "City",
  state: "State",
  location: "Location",
  useThisLocation: "Use this location",
  useAsAddress: "Use as address",
  locationNotFound: "Location not found",
  timeZone: "Time zone",
  sameAsCompany: (zone) => `Same as the company: ${zone}`,
  zone: (name) => ZONE_NAMES.get(name) ?? name.replaceAll("_", " "),
  cityState: (city, state) => `${city}, ${state}`,
  closeShop: (name) => `Close ${name}`,
  closeShopButton: "Close shop",
  notAState: "Two letters",
  notAPosition: "Not latitude, longitude",
  latLng: "Latitude, longitude",
  clockInRadius: "Clock-in radius",
  metres: "m",
  radiusFromTo: (min, max) => `${String(min)} to ${String(max)} m`,
  mapOf: (place2, metres) => `${place2}, ${String(metres)} m`
};

// shared/strings/en.ts
var sized = (name, size) => `${name}, ${size}`;
var itemCount = (n) => n === 1 ? "1 item" : `${String(n)} items`;
var SIGNED_IN = { logOut: "Log out", timeLeft: (left) => `Time left ${left}`, firstAndInitial: (first, initial) => `${first} ${initial}.` };
var NO_TAX_RATE = "No tax rate";
var CUSTOMER_NEEDED = "Customer needed";
var REGISTER = {
  ...SIGNED_IN,
  signedInAs: "Signed in as",
  endingIn: (seconds) => `Ending in ${String(seconds)} seconds`,
  offline: (waiting) => waiting === 0 ? "Offline" : `Offline \xB7 ${String(waiting)} waiting`,
  devices: "Devices",
  scanner: "Scanner",
  printer: "Printer",
  reader: "Card reader",
  notConnected: "Not connected",
  deviceProblem: (device, problem) => `${device}: ${problem}`,
  paper: /* @__PURE__ */ new Map([["paper_low", "Paper low"], ["paper_out", "Paper out"], ["cover_open", "Cover open"], ["offline", "Not connected"], ["error", "Printer error"]]),
  pin: "PIN",
  enter: "Enter",
  digits: (n) => `${String(n)} digits`,
  wrongPin: "Wrong PIN",
  pinLocked: "PIN locked",
  padLocked: "iPad locked",
  unlockWithPhone: "Unlock with a phone",
  setUpAsRegister: "Set up as a register",
  stopped: "Register stopped",
  notFinished: "Sale not finished",
  partPaid: "Sale part paid",
  backToSale: "Back to sale",
  backToReturn: "Back to return",
  exchange: "Exchange",
  exchangeOpen: "Exchange open",
  back: "Back",
  cancel: "Cancel",
  done: "Done",
  send: "Send",
  tryAgain: "Try again",
  loading: "Loading",
  notLoaded: "Not loaded",
  searchItems: "Search items",
  all: "All",
  held: "Held",
  orders: "Orders",
  more: "More",
  returns: "Return",
  giftCard: "Gift card",
  drawer: "Drawer",
  status: "Status",
  categories: "Categories",
  items: "Items",
  sale: "Sale",
  noItems: "No items",
  nothingFound: "Nothing found",
  from: (price) => `From ${price}`,
  pickBy: (by) => `Pick ${/^[aeiou]/i.test(by) ? "an" : "a"} ${by.toLowerCase()}`,
  misc: "Misc item",
  miscName: "What it is",
  price: "Price",
  taxable: "Taxed",
  add: "Add",
  addCustomer: "Add customer",
  addNote: "Add note",
  note: "Note",
  onReceipt: "On receipt",
  noteOnReceipt: "Note \xB7 On receipt",
  hold: "Hold",
  charge: "Charge",
  heldSale: "Sale held",
  account: (balance) => `Account ${balance}`,
  scanned: (item, quantity) => `${item}: ${String(quantity)}`,
  unknownCode: (code) => `Unknown code ${code}`,
  line: sized,
  fewer: (item) => `One fewer ${item}`,
  more1: (item) => `One more ${item}`,
  remove: "Remove",
  removeItem: (item) => `Remove ${item}`,
  separator: " \xB7 ",
  times: (n, each2) => `${String(n)} \xD7 ${each2}`,
  heldFor: (customer, what) => `${customer} \xB7 ${what}`,
  quantity: (item) => `How many ${item}`,
  changeLine: (item) => `Change ${item}`,
  lineDiscount: "Discount",
  percent: (p) => `${String(p)}%`,
  amountOff: "$ off",
  percentOff: "% off",
  promo: "Promo",
  off: (what) => `${what} off`,
  addDiscount: "Add discount",
  applyTo: "Apply to",
  thisItem: "This item",
  wholeSale: "Whole sale",
  saleDiscount: "Sale discount",
  removeDiscount: "Remove discount",
  reason: "Why",
  lineAmount: "Line",
  discount: "Discount",
  typedDiscount: (what) => `Discount ${what}`,
  subtotal: "Subtotal",
  tax: "Tax",
  total: "Total",
  leftToPay: "Left to pay",
  totals: "Totals",
  sizes: "Sizes",
  customer: "Customer",
  searchCustomers: "Search customers",
  newCustomer: "New customer",
  name: "Name",
  email: "Email",
  phone: "Phone",
  remove1: "Remove from sale",
  payOnAccount: "Pay on account",
  paying: "Paying",
  allOwed: (amount) => `All ${amount}`,
  credit: "Credit",
  noCustomers: "No customers found",
  owed: "Owed",
  limit: "Limit",
  history: "History",
  payBy: "Pay by",
  cash: "Cash",
  card: "Card",
  account1: "Account",
  phonePay: "Phone",
  due: "Due",
  left: "Left",
  given: "Given",
  change: "Change",
  exact: "Exact",
  take: (amount) => `Take ${amount}`,
  amountTyped: "Amount",
  tapCard: "Tap, insert or swipe",
  approving: "Approving",
  declined: (why) => `Declined: ${why}`,
  onThisCard: "On this card",
  cardSays: /* @__PURE__ */ new Map([
    ["insufficient_funds", "insufficient funds"],
    ["expired_card", "card expired"],
    ["incorrect_cvc", "wrong code"],
    ["lost_card", "card lost"],
    ["stolen_card", "card stolen"],
    ["do_not_honor", "not honored"]
  ]),
  declinedOther: "declined by the bank",
  cardRefused: /* @__PURE__ */ new Map([
    ["reader_offline", "Reader not connected"],
    ["reader_timeout", "Reader not answering"],
    ["reader_busy", "Reader busy"],
    ["no_reader", "No card reader"],
    ["stripe_unreachable", "Not sent"],
    ["card_refused", "Card not taken"],
    ["card_done", "Already paid"],
    ["moto_not_enabled", "Keyed cards not turned on"]
  ]),
  giftCardCode: "Gift card code",
  balance: "Balance",
  pays: "Pays",
  leftOnCard: "Left on card",
  pay: (amount) => `Pay ${amount}`,
  unknownCard: "Unknown card",
  frozen: "Card frozen",
  noBalance: "No balance",
  lookUp: "Look up",
  owedNow: "Owed now",
  after: "After",
  chargeToAccount: "Charge to account",
  overLimit: "Over the limit",
  phoneCode: "Code from the phone",
  typeCode: "Type the code",
  wrongCode: "Wrong code",
  scanToPay: "Scan to pay",
  print: "Print",
  noReceipt: "No receipt",
  text: "Text",
  newSale: "New sale",
  printed: "Printed",
  notPrinted: "Not printed",
  printUncertain: "Print not confirmed",
  sent: "Sent",
  notSent: "Not sent",
  receiptTo: "Send receipt",
  receiptWord: "Receipt",
  receiptWaiting: "Receipt waiting to send",
  noTaxRate: NO_TAX_RATE,
  noHeld: "No held sales",
  heldBy: (who, when, register) => [who, when, register].filter((x) => x !== "").join(" \xB7 "),
  heldItems: itemCount,
  pastDays: "Past its days",
  claimed: "Taken by another register",
  cleared: "Sale cleared",
  clear: "Clear sale",
  searchOrders: "Number, amount or last four",
  today: "Today",
  yesterday: "Yesterday",
  pickDay: "Pick a day",
  noOrders: "No orders",
  day: "Day",
  reprint: "Reprint",
  refund: (amount) => `Refund ${amount}`,
  refundTotal: "Refund",
  refundTo: "Refund to",
  storeCredit: "Store credit",
  bought: (n) => `Bought ${String(n)}`,
  returned: "Returned",
  newItems: "New items",
  exchangeFor: "Exchange for",
  exchanged: "Exchanged",
  notReceiptNumber: "Not a receipt number",
  returnDone: "Refunded",
  noReceiptReturn: "Return without receipt",
  approve: "Approve",
  approveLabel: "Lead PIN",
  approves: {
    price: "Change a price",
    discount: "Give a discount",
    return: "Return",
    noReceipt: "Return without receipt",
    otherTender: "Refund to another way",
    clear: "Clear a held sale",
    payIn: "Pay in",
    payOut: "Pay out",
    open: "Open drawer",
    move: "Move to store",
    topUp: "Top up a gift card"
  },
  sellCard: "Sell a card",
  topUp: "Top up",
  cardAdded: "Gift card added",
  payIn: "Pay in",
  payOut: "Pay out",
  moveToStore: "Move to store",
  openDrawer: "Open drawer",
  whatFor: "What for",
  paidIn: (amount) => `Paid in ${amount}`,
  paidOut: (amount) => `Paid out ${amount}`,
  moved: (amount) => `Moved ${amount}`,
  drawerOpened: "Drawer opened",
  heldForReview: "Held for review",
  resync: "Reload items",
  resyncing: "Reloading",
  resynced: "Reloaded",
  notResynced: "Not reloaded",
  connection: "Connection",
  online: "Online",
  waitingToSend: "Waiting to send",
  notTaken: (why) => `Not taken: ${why}`,
  sendNow: "Send now",
  shopBox: "Local server",
  scanners: "Scanners",
  version: "Version",
  update: "Update",
  ok: "OK",
  connected: "Connected",
  notSeen: "Not seen",
  none: "None",
  phoneOrder: "Phone order",
  orderNumber: "Order",
  textLink: "Text link",
  emailLink: "Email link",
  linkSent: "Link sent",
  giveCode: "Give code",
  newCode: "New code",
  goodFor: "Good for 30 minutes",
  codeExpired: "Code expired",
  codeEnded: "Code ended",
  keyCard: "Key card",
  codeGiven: "Code given",
  phoneHeld: "Phone",
  orderPaid: (n) => `Order paid: ${n}`,
  notThem: "Not them",
  customerAdded: (name) => `${name} added`,
  cardOnFile: (name) => `Card on file for ${name}`,
  moveTo: (name) => `Move to ${name}`,
  keep: "Keep",
  cardMoved: "Card moved",
  cardOnFileRow: "Card on file",
  askOnReader: "Ask on reader",
  onReader: { email: "Email on reader", phone: "Phone on reader" },
  waitingOnReader: "Waiting on reader",
  skipped: "Skipped",
  notEntered: "Not entered",
  mailingList: "Mailing list",
  customerType: "Type",
  customerNeeded: CUSTOMER_NEEDED,
  problems: /* @__PURE__ */ new Map([
    ["not_found", "Not found"],
    ["not_allowed", "Not allowed"],
    ["taken", "Taken"],
    ["may_not_charge", "Account not allowed"],
    ["totals", "Prices changed"],
    ["stale", "Changed on another register"],
    ["not_sent", "Not sent"],
    ["sale_open", "Sale open"],
    ["over_limit", "Over the limit"],
    ["frozen", "Card frozen"],
    ["no_balance", "No balance"],
    ["not_approved", "Not approved"],
    ["invalid", "Not accepted"],
    ["override_needed", "Price override needed"],
    ["needs_customer", CUSTOMER_NEEDED],
    ["receiptless_needs_customer", CUSTOMER_NEEDED],
    ["held", "Sale held"],
    ["finished", "Sale closed"],
    ["repriced", "Tax rate changed"],
    ["unpaid", "Not paid in full"],
    ["payment_pending", "Payment pending"],
    ["not_held", "Not held"],
    ["not_phone_order", "Not a phone order"],
    ["not_offered", "Not offered here"],
    ["too_many", "Too many"],
    ["no_stripe", "No card payments"],
    ["other_type", "Other customer type"],
    ["not_open_to_customer", "Sale has a customer"],
    ["no_tax_rate", NO_TAX_RATE]
  ])
};
var PAGE_WORDS = { close: "Close", moreActions: "More actions", moreActionsFor: (item) => `More actions: ${item}`, digit: (n) => `Digit ${String(n)}` };
var OFFICE = {
  ...SHOP_WORDS,
  ...PAGE_WORDS,
  open: "Open",
  app: "Register",
  required: "Required",
  name: "Name",
  save: "Save",
  saved: "Saved",
  cancel: "Cancel",
  edit: "Edit",
  editNamed: (what) => `Edit ${what}`,
  show: "Show",
  shop: "Shop",
  approve: "Approve",
  decline: "Decline",
  approved: "Approved",
  declined: "Declined",
  onHold: "On hold for 24 hours",
  separator: " \xB7 ",
  loading: "Loading",
  offline: "Offline",
  tryAgain: "Try again",
  notLoaded: "Not loaded",
  newVersion: "New version",
  update: "Update",
  updateRequired: "Update required",
  undo: "Undo",
  onlyOwnerApproves: (shop, people) => `${shop}: only the Owner approves (${String(people)})`,
  passkeys: "Passkeys",
  noPasskeys: "No passkeys",
  devices: "Devices",
  noDevices: "No devices",
  remove: "Remove",
  removeItem: (item) => `Remove ${item}`,
  signOut: "Sign out",
  signOutNamed: (name) => `Sign out ${name}`,
  added: (when) => `Added ${when}`,
  used: (when) => `Used ${when}`,
  seen: (when) => `Seen ${when}`,
  waiting: "Waiting",
  thisDevice: "This device",
  preferredName: "Preferred name",
  email: "Email",
  phone: "Phone",
  notAPhone: "Not a phone number",
  back: "Back",
  signIn: "Sign in with a passkey",
  emailCode: "Email me a code",
  textCode: "Text me a code",
  sendCode: "Send code",
  codeSent: "Code sent",
  codeSentTo: (email) => `Code sent to ${email}`,
  codeSentByText: "Code sent by text",
  signInCode: "Sign-in code",
  signInWithCode: "Sign in",
  sendAgain: "Send again",
  addPasskey: "Add a passkey",
  checkAgain: "Check again",
  waitingForOwner: "Waiting for another Owner",
  ownerOf: (company2) => `Owner, ${company2}`,
  accepted: "Accepted",
  notAccepted: "Not accepted",
  logo: "Logo",
  uploadLogo: "Upload logo",
  removeLogo: "Remove logo",
  companyColour: "Company colour",
  sharedIpadColour: "Register colour",
  clockIn: "Charge",
  signedInAs: "Signed in as",
  sampleTime: "9:41",
  accountSid: "Account SID",
  keySid: "API key SID",
  keySecret: "API key secret",
  serviceSid: "Verify service SID",
  checkedOn: (when) => `Checked ${when}`,
  setOn: (when) => `Set ${when}`,
  choose: "Choose",
  connect: "Connect",
  connectOn: (host) => `Connect on ${host}`,
  disconnect: "Disconnect",
  disconnectGmail: "Disconnect Gmail",
  gmail: "Gmail",
  twilio: "Twilio",
  noTextNumber: "No number",
  notAvailable: "Not available",
  notChecked: "Not checked",
  notSet: "Not set",
  notWorking: "Not working",
  works: "Works",
  number: "Number",
  sendingAddress: "Sending address",
  textsFrom: "Texts from",
  twilioNotReached: "Twilio not reached",
  saveNumber: "Save number",
  codeFor: (email) => `Code: ${email}`,
  confirm: "Confirm",
  newPin: "New PIN",
  pinAgain: "New PIN again",
  pinsDiffer: "Not the same PIN",
  onHoldUntil: (when) => `On hold until ${when}`,
  pin: "PIN",
  pinForDevices: "PIN for registers",
  pinSet: "PIN set",
  pinNotSet: "PIN not set",
  pinStates: { none: "Not set", set: "Set", locked: "Locked" },
  profile: "Profile",
  setPin: "Set PIN",
  theme: "Theme",
  ipadSettings: "iPad settings",
  noPinsSet: "No PINs set",
  setPinFor: "Set a PIN",
  removeIpad: "Remove this iPad",
  setWithPhone: "Set with a phone",
  removeWithPhone: "Remove with a phone",
  noPeopleWithoutPhone: "No people without a phone",
  addToHomeScreen: "Add to Home Screen",
  blocked: "Blocked in Settings",
  checkDone: "Done",
  homeScreenSteps: ["Tap Share", "Tap Add to Home Screen", "Open Register from the Home Screen"],
  setupSteps: [],
  setupChecks: { homeScreen: "Add Register to the Home Screen", location: "Allow location" },
  nothingToSetUp: "Nothing to set up",
  setUpThisDevice: "Set up this device",
  setupCode: "Setup code",
  newDevice: "New device",
  newCode: "New code",
  notSetUpYet: "Not set up",
  shopDevice: (shop) => `${shop} register`,
  add: "Add",
  validUntil: (time) => `Valid until ${time}`,
  openOnDevice: "Open on the register",
  deviceAddress: "Register address",
  copy: "Copy",
  copied: "Copied",
  closed: "Closed",
  invite: "Invite",
  inviteSent: "Invite sent",
  invited: "Invited",
  left: "Left",
  noPeople: "No people",
  passkeysWaiting: "Passkeys waiting",
  role: "Role",
  searchPeople: "Search people",
  sendInvite: "Send invite",
  shops: "Shops",
  everyShop: "Every shop",
  deviceName: "Register name",
  noSharedDevices: "No registers",
  stopped: "Stopped",
  personLeft: (name) => `${name} left`,
  rename: "Rename",
  renameItem: (item) => `Rename ${item}`,
  setUpBy: (name) => `Set up by ${name}`,
  removed: "Removed",
  digits: (n) => `${String(n)} digits`,
  digitsUnit: "digits",
  between: (low, high, unit) => `${low} to ${high} ${unit}`,
  eraseDigit: "Erase a digit",
  roles: { owner: "Owner", manager: "Manager", lead: "Staff Lead", staff: "Staff" },
  roleCan: (what, role) => `${what}: ${role}`,
  peopleAndCompany: "People and company",
  whatPeopleCanDo: "What people can do",
  ...SIGNED_IN,
  themes: { auto: "Automatic", light: "Light", dark: "Dark" },
  perms: {
    platform: {
      "people.view": "See people",
      "people.invite": "Invite and rehire people",
      "people.change_role": "Change roles and shops",
      "people.deactivate": "Mark people as left",
      "people.edit_contact": "Correct contact details",
      "people.issue_code": "Give sign-in codes in person",
      "people.approve_owner_passkey": "Approve an Owner's new passkey",
      "devices.setup_shared": "Set up registers",
      "devices.manage": "Remove passkeys, devices and registers",
      "locations.manage": "Change shops",
      "company.look": "Change the company look",
      "company.services": "Connect Gmail and Twilio",
      "company.permissions": "Change people and company permissions"
    }
  },
  checks: {
    buttons: (theme, ratio, needs) => `${theme}: buttons on the page, ${ratio}:1 (needs ${String(needs)}:1)`,
    onButtons: (theme, ratio, needs) => `${theme}: text on buttons, ${ratio}:1 (needs ${String(needs)}:1)`,
    fromShared: (theme) => `${theme}: apart from the register colour`,
    bar: (theme, ratio, needs) => `${theme}: bar on the page, ${ratio}:1 (needs ${String(needs)}:1)`,
    onBar: (theme, ratio, needs) => `${theme}: text on the bar, ${ratio}:1 (needs ${String(needs)}:1)`,
    fromBrand: (theme) => `${theme}: apart from the company colour`,
    allPass: "Passes every check, light and dark"
  },
  problems: {
    too_large: "Too large",
    unreadable: "Photo not readable",
    lost: "Photo not taken",
    not_converted: "Photo not taken",
    not_a_photo: "Not a photo",
    storage_full: "Storage full",
    photo_not_found: "Photo not found",
    template_photo: "Photos go on items",
    not_found: "Not found",
    not_allowed: "No access",
    step_up: "Passkey check needed",
    link_expired: "Link expired",
    failed: "Not done",
    invalid: "Not valid",
    not_sent: "Not sent",
    override_needed: "Price override needed",
    busy: "Busy",
    conflict: "Changed elsewhere",
    not_approved: "Not approved",
    update_required: "Update required",
    offline: "Offline",
    too_many: "Too many tries",
    no_stripe: "No Stripe key",
    stripe_unreachable: "Stripe not reached",
    reader_unknown: "No reader with that number",
    key_unknown: "Key not accepted",
    key_changed: "Key changed",
    webhook_refused: "Webhook not made",
    no_settings: "Not set up",
    default_needed: "A default type is needed",
    is_default: "The default type",
    rate_overlap: "Overlaps another rate",
    location_taken: "Location linked to another shop",
    shop_linked: "Shop has a location",
    not_made: "Shop not made",
    left_to_stripe: "Left to Stripe",
    shop_has_readers: "Shop has card readers",
    no_evidence: "No evidence",
    submitted: "Already submitted",
    file_refused: "Receipt not taken",
    dispute_refused: "Not taken by Stripe",
    template_sizes: "Sizes come from its template",
    not_count: "Not sold each",
    several_sizes: "More than one size",
    shop_address: "Shop address missing",
    location_refused: "Location not made",
    no_terminal_location: "No Stripe location",
    registration_refused: "Code not taken",
    reader_not_found: "Reader not found",
    reader_elsewhere: "Reader at another location",
    reader_taken: "Reader on another register",
    restart_window: "Different hours",
    in_group: "Already in a group",
    configuration_refused: "Not taken by Stripe",
    serial_or_code: "Serial number or code needed",
    other_form_taken: "Barcode on another item",
    file_kind: "Not a photo",
    templates_differ: "Items of different templates",
    group_in_group: "A group in a group",
    not_a_group: "Not a group",
    group_archived: "Group archived",
    name_too_long: "Name too long with its group",
    group_size: "Size of a group",
    not_its_group: "Size of another group",
    group_has_items: "Group has items"
  },
  reports: {
    names: {
      day_sales: "The day's sales",
      card_stripe: "Card payments and Stripe",
      cash_count: "Cash count",
      cogs: "Cost of goods and margin",
      tax: "Tax",
      outstanding: "Gift card and account balances"
    },
    columns: {
      business_date: "Day",
      shop: "Shop",
      register: "Register",
      sales: "Sales",
      returns: "Returns",
      corrections: "Corrections",
      gross_sales: "Gross sales",
      discounts: "Discounts",
      returns_receipt: "Returns with receipt",
      returns_no_receipt: "Returns without receipt",
      net_sales: "Net sales",
      untaxed_sales: "Untaxed sales",
      tax_collected: "Tax collected",
      tax_refunded: "Tax refunded",
      gift_card_loads: "Gift cards loaded",
      account_charges: "Charged to accounts",
      account_paydowns: "Paid on accounts",
      cash: "Cash",
      card: "Card",
      phone: "Phone payment",
      gift_card: "Gift card",
      account: "Account",
      rounding: "Rounding",
      overpayments: "Overpayments",
      disputes: "Disputes",
      receipt: "Receipt",
      what: "What",
      pos_amount: "Card sales",
      stripe_amount: "Stripe",
      difference: "Difference",
      fee: "Fees",
      paid_out: "Paid out",
      stripe_ref: "Stripe reference",
      movement: "Cash in and out",
      moved: "Moved to store",
      expected: "Expected",
      counted: "Counted",
      over_short: "Over or short",
      drawer: "Drawer",
      store: "Store",
      deposits: "Deposits",
      product: "Item",
      sales_costed: "Sales with a cost",
      cost_of_goods: "Cost of goods",
      margin: "Margin",
      margin_pct: "Margin %",
      rate: "Rate",
      taxable_sales: "Taxed sales",
      net_tax: "Tax owed",
      cards_loaded: "Loaded on gift cards",
      cards_spent: "Spent from gift cards",
      cards_outstanding: "On gift cards",
      accounts_charged: "Added to accounts",
      accounts_paid: "Paid or credited",
      accounts_owed: "Owed on accounts"
    },
    words: { charge: "Charge", refund: "Refund", dispute: "Dispute", unmatched: "Unmatched", payout: "Payout" },
    total: "Total",
    percent: (n) => `${n}%`
  },
  amount: "Amount",
  notAnAmount: "Not an amount",
  dash: "\u2013",
  dollars: "$",
  percentSign: "%",
  tenders: { cash: "Cash", card: "Card", gift_card: "Gift card", account: "Account", phone: "Phone payment" },
  sales: {
    days: "Days",
    spans: { today: "Today", yesterday: "Yesterday", week: "This week", pick: "Pick dates" },
    allShops: "All shops",
    from: "From",
    to: "To",
    salesFigure: "Sales",
    orders: "Orders",
    average: "Average",
    returns: "Returns",
    tax: "Tax",
    howPaid: "How paid",
    amount: "Amount",
    reports: "Reports",
    csv: "Download CSV",
    print: "Print",
    noSales: "No sales",
    missing: {
      title: "Before the first sale",
      register: "Add a register",
      taxRate: (shop) => `Tax rate: ${shop}`,
      product: "Add an item"
    }
  },
  orders: {
    search: "Number, amount or last four",
    none: "No orders",
    noneFound: "Nothing found",
    noShops: "No shops",
    cardEnding: (way, last4) => `${way} \u2022\u2022${last4}`,
    lines: "Items",
    payments: "Payments",
    customer: "Customer",
    showMore: "Show more",
    subtotal: "Subtotal",
    discounts: "Discounts",
    tax: "Tax",
    total: "Total",
    sized,
    quantity: (n) => `\xD7 ${n}`,
    refund: "Refund to card",
    refunded: "Refunded",
    correction: "Taken by mistake",
    hold: "Hold",
    held: "Held",
    note: "Note",
    giveBack: "Give back as cash",
    givenBack: "Given back",
    kinds: { return: "Return", correction: "Correction" }
  },
  items: {
    range: (low, high) => `${low} \u2013 ${high}`,
    count: itemCount,
    newItem: "New item",
    unknownCodes: "Unknown codes",
    categories: "Categories",
    search: "Search items or barcodes",
    category: "Category",
    all: "All",
    none: "No items",
    noneFound: "Nothing found",
    item: "Item",
    sizes: "Sizes",
    newCategory: "New category",
    sized,
    give: "Give to this size",
    group: "Group",
    newGroup: "New group",
    pickTwo: "Two or more items",
    findItems: "Search items",
    groupOf: (n) => `Group \xB7 ${itemCount(n)}`,
    size: "Size",
    noUnknown: "No unknown codes",
    scanned: (n) => n === 1 ? "Scanned once" : `Scanned ${String(n)} times`,
    given: "Code given",
    templates: "Templates",
    newTemplate: "New template",
    noTemplates: "No templates",
    sizeCount: (n) => n === 1 ? "1 size" : `${String(n)} sizes`
  },
  item: {
    notValid: "Not valid",
    size: "Size",
    units: "Units",
    barcode: "Barcode",
    cell: (column, size) => `${column}, ${size === "" ? "new size" : size}`,
    sizes: "Sizes",
    newItem: "New item",
    archive: "Archive",
    archived: "Archived",
    category: "Category",
    noCategory: "No category",
    soldBy: "Sold by",
    taxed: "Taxed",
    template: "Template",
    noTemplate: "No template",
    newTemplate: "New template",
    items: "Items",
    group: "Group",
    noGroup: "No group",
    addGroup: "Add group",
    newGroup: "New group",
    variesBy: "Varies by",
    groupName: "Group name",
    addToItems: (n) => n === 1 ? "Add to 1 item" : `Add to ${String(n)} items`,
    addedTo: (n) => n === 1 ? "Added to 1 item" : `Added to ${String(n)} items`,
    addSize: "Add size",
    newSize: "New size",
    priceAt: (level, price) => `${level} ${price}`,
    done: "Done",
    oneSize: "Each",
    addItem: "Add item",
    photo: "Photo",
    addPhoto: "Add photo",
    changePhoto: "Change photo",
    removePhoto: "Remove photo",
    unitNames: { oz: "oz", lb: "lb", g: "g", kg: "kg", each: "each", fl_oz_us: "fl oz", mL: "mL", L: "L" }
  },
  customers: {
    newCustomer: "New customer",
    kind: "Customer",
    person: "Person",
    business: "Business",
    businessName: "Business name",
    contact: "Contact name",
    type: "Type",
    mailing: "Mailing list",
    merge: "Merge",
    mergeFrom: "Merge",
    mergeInto: "Into",
    sameCustomer: "The same customer",
    merged: "Merged",
    count: (n) => n === 1 ? "1 customer" : `${String(n)} customers`,
    search: "Name, email or phone",
    all: "All",
    none: "No customers",
    noneFound: "Nothing found",
    owed: "Owed",
    limit: "Limit",
    details: "Details",
    yes: "On",
    no: "Off",
    history: "History",
    noSales: "No sales",
    ledger: "Account",
    creditLimit: "Credit limit",
    typeLimit: (type, amount) => `${type}: ${amount}`,
    entry: { sale: "Charged", payment: "Paid", refund: "Refund", opening: "Opening balance", merge: "Merged", adjustment: "Adjustment" }
  },
  pricing: {
    percentOff: (n) => `${n}% off`,
    amountOff: (amount) => `${amount} off`,
    newType: "New type",
    level: "Price level",
    discount: "Discount",
    noDiscount: "No discount",
    taxed: "Taxed",
    mayCharge: "May charge to an account",
    default: "Default",
    newLevel: "New price level",
    newPromo: "New promo",
    off: "Off",
    percent: "Percent",
    amount: "Amount",
    stacks: "Stacks with others",
    showOnPos: "Show on POS",
    notOnPos: "Not on\xA0POS",
    active: "Active",
    isOn: "On",
    ended: "Ended",
    from: "From",
    until: "Until",
    endsFirst: "Ends before it starts",
    starts: (day) => `Starts ${day}`,
    dates: (from, until) => `${from} to ${until}`,
    fromOnly: (day) => `From ${day}`,
    untilOnly: (day) => `Until ${day}`
  },
  settings: {
    shops: "Shops",
    shop: "Shop settings",
    pos: "Register settings",
    stripe: "Stripe",
    customers: "Customer settings",
    types: "Customer types",
    levels: "Price levels",
    look: "Company look",
    services: "Gmail and Twilio",
    permissions: "Permissions",
    rate: (percent) => `${percent}%`,
    newRate: "New tax rate",
    rateLabel: "Rate",
    notARate: "Not a rate",
    taxRates: "Tax rates",
    overlaps: (rate, shop) => `Overlaps ${rate} at ${shop}`,
    from: "From",
    to: "To",
    tax: "Tax",
    receipts: "Receipts",
    receiptText: "Receipt text",
    autoPrint: "Print by itself",
    cash: "Cash",
    rounding: "Round cash to 5\xA2",
    reader: "Card reader",
    stripeLocation: "Stripe location",
    none: "None",
    linked: "Linked",
    defaultType: "Customer type of a new sale",
    heldDays: "Keep held sales",
    days: "days",
    notDays: "1 to 365 days",
    checkAndSave: "Check and save",
    key: "Restricted key",
    keySet: (when) => `Set ${when}`,
    missing: "Missing from key",
    events: "Updates from Stripe",
    webhook: "Payments, refunds and payouts",
    lastEvent: (when) => `Last update ${when}`,
    notSet: "Not set",
    failing: "Failing",
    working: "Working",
    atRegister: "At the register",
    inBackOffice: "In the back office",
    postalCode: "ZIP code",
    notAPostalCode: "ZIP or ZIP+4",
    makeLocation: "Make Stripe location",
    locationMade: "Stripe location made",
    restartFrom: "Readers restart from",
    restartTo: "Readers restart until",
    newPasscode: "New reader passcode",
    makePasscode: "New passcode",
    giftCards: "Gift card codes",
    prefix: "Starts with",
    notAPrefix: "Letters, digits or marks, no spaces",
    length: "Length",
    characters: "characters",
    notALength: (min) => `${String(min)} to ${String(GIFT_CARD_CODE_MAX)}`,
    clearForm: "Clear",
    clearCodes: "Clear code format",
    saveCodes: "Save code format",
    stripeNames: {
      billing_meter: "Billing Meters",
      coupon: "Coupons",
      credit_note: "Credit Notes",
      customer_portal: "Customer Portal",
      entitlement: "Entitlements",
      invoice: "Invoices",
      plan: "Prices",
      promotion_code: "Promotion Codes",
      quote: "Quotes",
      subscription: "Subscriptions",
      tax_rate: "Tax Rates",
      billing_clock: "Test Clocks",
      checkout_session: "Checkout Sessions",
      application_fee: "Application Fees",
      top_up: "Top-Ups",
      transfer: "Transfers",
      connected_account: "Accounts",
      apple_pay_domain: "Apple Pay Domains",
      balance: "Balance",
      charge: "Charges and Refunds",
      confirmation_token: "Confirmation Tokens",
      customer: "Customers",
      event: "Events",
      file: "Files",
      mandate: "Mandates",
      dispute: "Payment Disputes",
      payment_intent: "Payment Intents",
      payment_links: "Payment Links",
      payment_method_configurations: "Payment Method Configurations",
      payment_method_domain: "Payment Method Domains",
      payment_method: "Payment Methods",
      payment_records: "Payment Records",
      payout: "Payouts",
      product: "Products",
      setup_intent: "Setup Intents",
      shipping_rate: "Shipping Rates",
      source: "Sources",
      token: "Tokens",
      issuing_authorization: "Issuing Authorizations",
      issuing_cardholder: "Issuing Cardholders",
      issuing_card: "Issuing Cards",
      issuing_dispute: "Issuing Disputes",
      issuing_token: "Issuing Tokens",
      issuing_transaction: "Issuing Transactions",
      review: "Reviews",
      report_runs_and_report_types: "Financial Reports",
      tax_calculations_and_transactions: "Tax Calculations, Transactions",
      tax_settings: "Tax Settings, Registrations",
      terminal_configuration: "Terminal Configurations",
      terminal_location: "Terminal Locations",
      terminal_reader: "Terminal Readers",
      treasury_transaction: "Treasury Transactions",
      webhook: "Webhook Endpoints, Event Destinations"
    },
    stripeLevels: { read: "read", write: "write" },
    held: (name, level) => `${name}: ${level}`,
    notNeeded: "Not needed by key",
    perms: {
      "pos.sell": "Ring up sales",
      "pos.gift_card.sell": "Sell gift cards",
      "pos.gift_card.top_up": "Top up gift cards",
      "pos.return": "Returns with a receipt",
      "pos.return.no_receipt": "Returns without a receipt",
      "pos.refund.other_tender": "Refund a card sale in cash or credit",
      "pos.price_override": "Change a price",
      "pos.discount.any": "Any discount",
      "pos.held.clear": "Clear others' held sales",
      "pos.gift_card.void": "Void gift cards",
      "pos.drawer.open": "Open the drawer",
      "pos.drawer.pay": "Pay in and out",
      "pos.cash.move": "Move cash to the store",
      "pos.orders.view": "See orders",
      "pos.attention": "Needs attention",
      "pos.refund.card": "Refund to a card",
      "pos.corrections": "Money taken by mistake",
      "pos.cash.deposit": "Deposits",
      "pos.catalog.edit": "Change items",
      "pos.customers.edit": "Change customers",
      "pos.customers.merge": "Merge customers",
      "pos.pricing.edit": "Change prices and promos",
      "pos.reports": "Reports",
      "pos.registers.manage": "Registers and scanners",
      "pos.settings": "Register settings",
      "pos.stripe": "Stripe",
      "pos.permissions": "Change register permissions"
    }
  },
  money: {
    kinds: {
      cash_sale: "Cash sales",
      cash_refund: "Cash refund",
      pay_in: "Paid in",
      pay_out: "Paid out",
      rounding: "Rounding",
      transfer: "Moved",
      deposit: "Deposit",
      over_short: "Count: over or short"
    },
    places: { drawer: "Drawer", store: "Store" },
    slip: (ref) => `Slip ${ref}`,
    noEntries: "No cash entries",
    entries: "Cash entries",
    when: "When",
    what: "What",
    by: "By",
    moveToStore: "Move to store",
    move: "Move",
    moved: "Moved to store",
    deposit: "Deposit",
    slipLabel: "Bank slip",
    deposited: "Deposited",
    frozen: "Frozen",
    cardSearch: "Last four of the card",
    noCards: "No gift cards",
    card: "Card",
    sold: "Sold",
    loaded: "Loaded",
    balance: "Balance",
    used: "Last used",
    state: "State",
    ending: (last4) => `\u2022\u2022${last4}`,
    freeze: "Freeze",
    unfreeze: "Unfreeze",
    balanceOf: (amount) => `Balance ${amount}`,
    unfrozen: "Unfrozen",
    noCardEntries: "No entries",
    sources: { sale: "Sold", redeem: "Spent", refund: "Refunded to the card", void: "Voided", opening: "Opening balance" }
  },
  hardware: {
    newRegister: "New register",
    code: "Code",
    notACode: "Up to 3 letters",
    devices: "Devices",
    noDevices: "No devices",
    setUpDevice: "Set up a device",
    deviceCount: (n) => n === 1 ? "1 device" : `${String(n)} devices`,
    reader: "Card reader",
    addReader: "Add reader",
    changeReader: "Change reader",
    serial: "Serial number",
    readerCode: "Code on reader",
    readerBy: "Add by",
    readerAdded: "Reader added",
    readerChanged: "Reader changed",
    readerPasscode: "Reader passcode",
    device: (code) => `Device ${code}`,
    lastSeen: (when) => `Seen ${when}`,
    none: "No registers",
    noReader: "No reader",
    hasReader: "Card reader",
    noPrinter: "No printer",
    hasPrinter: "Printer",
    deviceFor: (register) => `Device for ${register}`,
    codeEnded: "Code ended",
    online: "Online",
    notSeen: "Not seen",
    scanners: "Scanners",
    connected: "Connected",
    notConnected: "Not connected",
    boxes: "Local scanning and printing servers",
    newBox: "New local server",
    noBoxes: "No local servers",
    notPaired: "Not paired",
    pair: "Pair",
    pairAgain: "Pair again",
    pairBox: (box2) => `Pair ${box2} again`,
    pairingCode: "Add-on pairing code",
    setUpIn: "Set up in",
    homeAssistant: "Home Assistant",
    noInternet: "No internet",
    box: "Local server",
    register: "Register",
    printer: "Printer",
    noRegister: "No register",
    newScanner: "New scanner",
    address: "Address",
    notAnAddress: "Not an address",
    port: "Port",
    notAPort: "1 to 65535",
    endsWith: "Code ends with",
    suffixes: { none: "None", auto: "Return or new line", cr: "Return", lf: "New line", crlf: "Return and new line", tab: "Tab" },
    printers: "Printers",
    newPrinter: "New printer",
    columns: "Columns",
    notColumns: "32 to 48",
    columnCount: (n) => `${String(n)} columns`,
    drawerPin: "Drawer pin",
    pin: (n) => `Pin ${String(n)}`,
    model: "Model",
    printerStates: { ready: "Ready", paper_low: "Paper low", paper_out: "Paper out", cover_open: "Cover open", offline: "Offline" },
    phone: "Phone",
    at: (host, port) => `${host}:${String(port)}`,
    foundAt: (host, port) => `Found at ${host}:${String(port)}`,
    addPhoneScanner: "Add a phone scanner",
    phoneScanner: "Phone scanner",
    openOnPhone: "Open on the phone",
    phoneLink: "Phone scanner link",
    notSetUp: "Not set up",
    printsFor: "Prints for"
  },
  stripe: {
    locations: "Stripe locations",
    noLocations: "No Stripe locations",
    linked: (shop) => `Shop: ${shop}`,
    notLinked: "Not linked",
    suggested: (shop) => `Suggested: ${shop}`,
    leftAlone: "Left alone",
    shop: "Shop",
    link: "Link",
    makeShop: "Make a shop",
    make: "Make shop",
    timeZone: "Time zone",
    leaveAlone: "Leave alone",
    takeBack: "Take back",
    unlink: "Unlink",
    chooseShop: "Choose a shop",
    linkShop: "Link to a shop",
    linkedPill: "Linked",
    linkedToast: "Linked",
    madeToast: "Shop made",
    leftToast: "Left alone",
    takenBackToast: "Taken back",
    unlinkedToast: "Unlinked",
    shopsMade: (n) => `Shops made: ${String(n)}`,
    readers: "Card readers",
    noReaders: "No card readers",
    offline: "Offline",
    logo: "Logo on readers",
    splash: { no_logo: "No logo", no_readers: "No readers set up", waiting: "Updating", shown: "Shown" },
    disputes: "Disputes",
    byStripe: "Stripe answers disputes",
    noDisputes: "No disputes",
    due: (when) => `Due ${when}`,
    pastDue: "Past due",
    closed: "Closed",
    evidence: { none: "No evidence", ready: "To review", submitted: "Sent" },
    reasons: {
      fraudulent: "Fraud",
      product_not_received: "Not received",
      product_unacceptable: "Not as described",
      duplicate: "Charged twice",
      credit_not_processed: "Refund not made",
      subscription_canceled: "Canceled",
      unrecognized: "Not recognized",
      general: "Other",
      customer_initiated: "Customer asked",
      debit_not_authorized: "Not authorized",
      incorrect_account_details: "Wrong account",
      insufficient_funds: "Insufficient funds",
      bank_cannot_process: "Bank could not process",
      check_returned: "Check returned"
    },
    dispute: "Dispute",
    amount: "Amount",
    dueBy: "Due by",
    reason: "Reason",
    words: {
      customer_name: "Customer",
      customer_email_address: "Email",
      service_date: "Date",
      product_description: "What was sold",
      uncategorized_text: "What happened"
    },
    receipt: "Receipt",
    submit: "Send to Stripe",
    sendFor: (amount) => `Send evidence for ${amount}`,
    submittedToast: "Sent",
    readerAsks: "Reader asks for",
    asks: { off: "Nothing", email: "Email", phone: "Phone", both: "Both" },
    sameAsCompany: "Same as company"
  },
  attention: {
    resolve: "Resolve",
    openOrder: "Open the order",
    openDispute: "Open the dispute",
    note: "Note",
    nothing: "Nothing needs attention",
    everyShop: "Every shop",
    urgent: "Urgent",
    review: "Review",
    resolved: "Resolved"
  },
  tabs: "Main",
  more: "More",
  nav: {
    sales: "Sales",
    orders: "Orders",
    items: "Items",
    customers: "Customers",
    attention: "Needs attention",
    promos: "Promos",
    giftCards: "Gift cards",
    cash: "Cash",
    registers: "Registers",
    scanners: "Scanners and printers",
    settings: "Settings"
  }
};
var SCANNER_PAGE = {
  ...PAGE_WORDS,
  title: "Scanner",
  sendTo: "Send to",
  camera: "Camera",
  photo: "Take a photo",
  noCode: "No code found",
  notACode: "Not a code",
  noRegisters: "No registers",
  sendAgain: "Send again",
  sent: (ending) => `Sent \u2026${ending}`,
  notTaken: (ending) => `Not taken \u2026${ending}`,
  notSent: (endings) => `Not sent: ${endings.map((e) => `\u2026${e}`).join(", ")}`,
  notLinked: "Not linked",
  linkCamera: "Scan the QR code from Add a phone scanner",
  notALink: "Not a scanner QR code",
  removed: "Scanner removed",
  notReached: "Not reachable",
  tryAgain: "Try again",
  addToHome: "Add to Home Screen"
};
var RECEIPT_PAGE = {
  ...PAGE_WORDS,
  title: "Receipt",
  number: (kind, number2) => `${kind ?? "Receipt"} ${number2}`,
  from: (shop, company2) => `${shop}, ${company2}`,
  under: (parts2) => parts2.join(" \xB7 "),
  totals: "Totals",
  paid: "Paid",
  notFound: "Receipt not found",
  notReached: "Not reachable",
  tryAgain: "Try again"
};
var PHONE = {
  ...PAGE_WORDS,
  title: "Pay",
  amount: "Amount",
  pay: "Pay",
  paid: "Paid",
  code: "Payment code",
  checking: "Checking payment",
  loading: "Loading",
  expired: "Link expired",
  alreadyPaid: "Already paid",
  notReached: "Not reachable",
  notValid: "Link not valid",
  tryAgain: "Try again",
  orderTitle: "Phone order",
  orderCode: "Order code",
  open: "Open",
  codeNotFound: "Code not found",
  notOpened: "Not opened",
  sixDigits: "6 digits",
  tooMany: "Too many tries"
};

// shared/receipt.ts
var GROUPED = new Intl.NumberFormat("en-US");
function wrapped(text3, width) {
  const out = [];
  for (const paragraph of text3.split("\n")) {
    let line = "";
    for (const word of paragraph.split(" ")) {
      const joined = line === "" ? word : `${line} ${word}`;
      if (joined.length <= width) line = joined;
      else {
        if (line !== "") out.push(line);
        for (line = word; line.length > width; line = line.slice(width)) out.push(line.slice(0, width));
      }
    }
    out.push(line);
  }
  return out;
}
function paired(row, columns) {
  const indent = /^ */.exec(row.left)?.[0] ?? "";
  const left = wrapped(row.left.slice(indent.length), Math.max(1, columns - row.right.length - 1 - indent.length)).map((l) => `${indent}${l}`);
  const last = left.pop() ?? "";
  const sub = row.sub === void 0 ? [] : wrapped(row.sub, columns - 2).map((l) => `  ${l}`);
  return [...left, `${last}${" ".repeat(Math.max(1, columns - last.length - row.right.length))}${row.right}`, ...sub];
}

// box/escpos.ts
var ESC = 27;
var GS = 29;
var DLE = 16;
var LF = 10;
var BARCODE_HEIGHT = 80;
var BAR_DOTS = 2;
var QUIET_MODULES = 20;
var INIT = [ESC, 64, GS, 97, 0, ESC, 116, 0];
var LEFT = [ESC, 97, 0];
var CENTER = [ESC, 97, 1];
var CUT = [GS, 86, 66, 0];
var STATUS_QUERY = Buffer.from([DLE, 4, 1, DLE, 4, 2, DLE, 4, 4]);
var PROCESSED_QUERY = Buffer.from([GS, 114, 1]);
function printerState(b) {
  if (b.length !== 3 || b.some((x) => (x & 147) !== 18)) return void 0;
  const [printer = 0, offline = 0, paper = 0] = b;
  if ((offline & 4) !== 0) return "cover_open";
  if ((paper & 96) !== 0 || (offline & 32) !== 0) return "paper_out";
  if ((printer & 8) !== 0) return "offline";
  return (paper & 12) !== 0 ? "paper_low" : "ready";
}
var drawerPulse = (pin) => Buffer.from([ESC, 112, pin === 2 ? 0 : 1, 25, 250]);
var ascii = (text3) => Buffer.from(text3.normalize("NFKD").replace(new RegExp("\\p{M}", "gu"), "").replace(/[^\x20-\x7e]/g, "?"), "latin1");
var lines = (all) => all.flatMap((l) => [ascii(l), Buffer.from([LF])]);
function raster(logo) {
  const bits = Buffer.from(logo.bits);
  for (let i = 0; i < bits.length - 1; i++) if (bits[i] === DLE && [4, 5, 20].includes(bits[i + 1] ?? 0)) bits[i] = DLE + 1;
  const across = logo.width / 8;
  return Buffer.concat([Buffer.from([...CENTER, GS, 118, 48, 0, across % 256, Math.floor(across / 256), logo.height % 256, Math.floor(logo.height / 256)]), bits, Buffer.from(LEFT)]);
}
function barcode(data2, columns) {
  const coded = `{B${data2.replaceAll("{", "{{")}`;
  const modules = 11 * (data2.length + 2) + 13 + QUIET_MODULES;
  if (modules * BAR_DOTS > columns * DOTS_PER_COLUMN) return Buffer.alloc(0);
  return Buffer.concat([Buffer.from([...CENTER, GS, 104, BARCODE_HEIGHT, GS, 119, BAR_DOTS, GS, 72, 0, GS, 107, 73, coded.length]), ascii(coded), Buffer.from(LEFT)]);
}
function text2(t, o) {
  const on = [...o.center ? CENTER : [], ...o.bold ? [ESC, 69, 1] : [], ...o.double ? [GS, 33, 17] : []];
  const off = [...o.double ? [GS, 33, 0] : [], ...o.bold ? [ESC, 69, 0] : [], ...o.center ? LEFT : []];
  return Buffer.concat([Buffer.from(on), ...lines(wrapped(t, o.double ? Math.floor(o.columns / 2) : o.columns)), Buffer.from(off)]);
}
function receiptBytes(doc, columns, logo) {
  return Buffer.concat([Buffer.from(INIT), ...doc.blocks.map((b) => {
    switch (b.t) {
      case "logo":
        return logo === null ? text2(b.text, { columns, center: true, bold: true, double: true }) : raster(logo);
      case "text":
        return text2(b.text, { columns, center: b.align === "center", bold: b.bold === true, double: b.size === "double" });
      case "rule":
        return Buffer.concat(lines(["-".repeat(columns)]));
      case "line":
        return Buffer.concat([...b.bold === true ? [Buffer.from([ESC, 69, 1])] : [], ...lines(paired(b, columns)), ...b.bold === true ? [Buffer.from([ESC, 69, 0])] : []]);
      case "barcode":
        return barcode(b.data, columns);
      case "feed":
        return Buffer.from([ESC, 100, b.lines]);
      case "cut":
        return Buffer.from(CUT);
    }
  })]);
}

// box/printer.ts
import { connect as connect2 } from "node:net";
var RETRY_MS = 500;
var STATUS_BYTES = 3;
var sleep = (ms) => new Promise((done) => setTimeout(done, ms));
var Wire = class {
  #got = Buffer.alloc(0);
  #wake;
  #closed = false;
  socket;
  constructor(socket) {
    this.socket = socket;
    socket.on("data", (b) => {
      this.#got = Buffer.concat([this.#got, b]);
      this.#wake?.();
    });
    socket.on("close", () => {
      this.#closed = true;
      this.#wake?.();
    });
    socket.on("error", () => void 0);
  }
  async read(n, ms) {
    const until = Date.now() + ms;
    while (this.#got.length < n && !this.#closed && Date.now() < until) {
      await new Promise((done) => {
        const late = setTimeout(done, until - Date.now());
        this.#wake = () => {
          clearTimeout(late);
          done();
        };
      });
    }
    this.#wake = void 0;
    if (this.#got.length < n) return void 0;
    const out = this.#got.subarray(0, n);
    this.#got = this.#got.subarray(n);
    return out;
  }
  async state(ms) {
    this.socket.write(STATUS_QUERY);
    const b = await this.read(STATUS_BYTES, ms);
    return b === void 0 ? void 0 : printerState(b);
  }
};
function attempt(at, ms) {
  return new Promise((done) => {
    const socket = connect2({ host: at.host, port: at.port });
    const late = setTimeout(() => {
      socket.destroy();
      done(void 0);
    }, ms);
    socket.once("connect", () => {
      clearTimeout(late);
      done(socket);
    });
    socket.once("error", () => {
      clearTimeout(late);
      done(void 0);
    });
  });
}
async function opened(at, t) {
  const until = Date.now() + t.connectForMs;
  for (; ; ) {
    const socket = await attempt(at, t.attemptMs);
    if (socket !== void 0) return new Wire(socket);
    if (Date.now() + RETRY_MS >= until) return void 0;
    await sleep(RETRY_MS);
  }
}
async function readState(at, t) {
  const w = await opened(at, t);
  if (w === void 0) return "offline";
  try {
    return await w.state(t.statusMs) ?? "ready";
  } finally {
    w.socket.destroy();
  }
}
async function runJob(at, bytes, t) {
  const w = await opened(at, t);
  if (w === void 0) return { outcome: { state: "failed", reason: "offline" }, state: "offline" };
  try {
    const before = await w.state(t.statusMs);
    if (before === "paper_out" || before === "cover_open" || before === "offline") return { outcome: { state: "failed", reason: before }, state: before };
    w.socket.write(Buffer.concat([bytes, PROCESSED_QUERY]));
    const processed = await w.read(1, t.processedMs);
    const after = processed === void 0 ? void 0 : await w.state(t.statusMs);
    return { outcome: { state: processed === void 0 ? "uncertain" : "printed" }, state: after ?? before ?? "ready" };
  } finally {
    w.socket.end();
  }
}

// box/printing.ts
var JOBS_KEPT = 1e3;
var FAILED = { state: "failed", reason: "error" };
var Printers = class {
  #held = /* @__PURE__ */ new Map();
  #jobs = /* @__PURE__ */ new Map();
  #logo = null;
  #o;
  constructor(o) {
    this.#o = o;
  }
  states() {
    return [...this.#held].map(([id, h]) => ({ id, state: h.state }));
  }
  apply(s) {
    this.#logo = s.logo === null ? null : { width: s.logo.width, height: s.logo.height, bits: Buffer.from(s.logo.bits, "base64") };
    for (const [id, h] of this.#held) {
      if (s.printers.some((p) => p.id === id)) continue;
      clearInterval(h.poll);
      this.#held.delete(id);
    }
    for (const p of s.printers) {
      const h = this.#held.get(p.id);
      if (h !== void 0) h.setting = p;
      else this.#held.set(p.id, { setting: p, state: "offline", chain: Promise.resolve(), poll: setInterval(() => this.#poll(p.id), this.#o.pollMs) });
      this.#poll(p.id);
    }
    this.#o.changed();
  }
  // A job for a printer the box doesn't hold isn't kept, so it prints when asked again once the settings name it
  job(m) {
    const known = this.#jobs.get(m.job);
    if (known !== void 0) return known;
    const h = this.#held.get(m.printer);
    if (h === void 0) return Promise.resolve(FAILED);
    const run = this.#run(h, m);
    this.#jobs.set(m.job, run);
    const [oldest] = this.#jobs.keys();
    if (this.#jobs.size > JOBS_KEPT && oldest !== void 0) this.#jobs.delete(oldest);
    return run;
  }
  stop() {
    for (const h of this.#held.values()) clearInterval(h.poll);
    this.#held.clear();
  }
  // Work on the printer after what is already queued for it, undefined if it threw
  #queue(h, work) {
    const run = h.chain.then(work).catch((err) => {
      log("printer_failed", { error: String(err) });
      return void 0;
    });
    h.chain = run.then(() => void 0);
    return run;
  }
  #set(h, state) {
    if (h.state === state) return;
    h.state = state;
    this.#o.changed();
  }
  #poll(id) {
    const h = this.#held.get(id);
    if (h === void 0) return;
    void this.#queue(h, async () => this.#set(h, await readState(h.setting, this.#o.timing)));
  }
  #run(h, m) {
    const bytes = m.type === "print" ? receiptBytes(m.doc, h.setting.columns, this.#logo) : drawerPulse(h.setting.drawerPin);
    return this.#queue(h, async () => {
      const r = await runJob(h.setting, bytes, this.#o.timing);
      this.#set(h, r.state);
      return r.outcome;
    }).then((o) => o ?? FAILED);
  }
};

// box/scanner.ts
import { connect as connect3 } from "node:net";
var ENDS = { auto: /\r\n|\r|\n/, cr: /\r/, lf: /\n/, crlf: /\r\n/, tab: /\t/ };
var PARTIAL_MAX = 256;
var JOIN_MS = 50;
var codes = (parts2) => parts2.map((p) => p.trim()).filter((code) => SCAN_CODE.test(code));
function framer(suffix) {
  let partial = "";
  const kept = (text3) => text3.length > PARTIAL_MAX ? "" : text3;
  if (suffix === "none") {
    return {
      chunk: (text3) => {
        partial = kept(partial + text3);
        return [];
      },
      flush: () => {
        const code = partial;
        partial = "";
        return codes([code]);
      }
    };
  }
  return {
    chunk: (text3) => {
      const parts2 = (partial + text3).split(ENDS[suffix]);
      partial = kept(parts2.pop() ?? "");
      return codes(parts2);
    },
    flush: () => []
  };
}
function watchScanner(at, t, h) {
  let stopped = false;
  let socket;
  let retry;
  let wait = t.firstMs;
  let last = { code: "", at: 0 };
  const took = (found) => {
    for (const code of found) {
      const now = Date.now();
      if (code === last.code && now - last.at < t.debounceMs) continue;
      last = { code, at: now };
      h.code(code);
    }
  };
  const open = () => {
    const frame = framer(at.suffix);
    let upAt;
    const s = connect3({ host: at.host, port: at.port });
    socket = s;
    s.setKeepAlive(true, t.steadyMs);
    s.on("connect", () => {
      upAt = Date.now();
      h.connected(true);
    });
    let join2;
    const ended = () => {
      clearTimeout(join2);
      const found = frame.flush();
      if (!stopped) took(found);
    };
    s.on("data", (b) => {
      took(frame.chunk(b.toString("latin1")));
      if (at.suffix !== "none") return;
      clearTimeout(join2);
      join2 = setTimeout(ended, JOIN_MS);
    });
    s.on("error", () => void 0);
    s.on("close", () => {
      ended();
      if (upAt !== void 0) h.connected(false);
      if (stopped) return;
      if (upAt !== void 0 && Date.now() - upAt >= t.steadyMs) wait = t.firstMs;
      retry = setTimeout(open, wait);
      wait = Math.min(wait * 2, t.maxMs);
    });
  };
  open();
  return {
    stop: () => {
      stopped = true;
      clearTimeout(retry);
      socket?.destroy();
    }
  };
}

// box/store.ts
import { generateKeyPairSync } from "node:crypto";
import { mkdirSync, readdirSync, readFileSync, renameSync, statSync, unlinkSync, writeFileSync } from "node:fs";
import { join } from "node:path";
var DAY_MS = 864e5;
var PRIVATE = 384;
var UUID_FILE = /^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.(sale|acked)$/;
function written(path, data2, mode = 420) {
  writeFileSync(`${path}.new`, data2, { mode });
  renameSync(`${path}.new`, path);
}
function readOrNone(path) {
  try {
    return readFileSync(path, "utf8");
  } catch {
    return void 0;
  }
}
var Store = class {
  dir;
  constructor(dir) {
    this.dir = dir;
    mkdirSync(join(dir, "journal"), { recursive: true });
    mkdirSync(join(dir, "tls"), { recursive: true });
  }
  key() {
    return readOrNone(join(this.dir, "key"))?.trim();
  }
  setKey(key) {
    written(join(this.dir, "key"), key, PRIVATE);
  }
  settings() {
    const raw = readOrNone(join(this.dir, "settings.json"));
    if (raw === void 0) return void 0;
    try {
      const m = toBox(raw);
      return m.type === "settings" ? m : void 0;
    } catch (err) {
      log("settings_unreadable", { error: String(err) });
      return void 0;
    }
  }
  setSettings(raw) {
    written(join(this.dir, "settings.json"), raw);
  }
  // The box's own key for the local path's certificate (P-256, PKCS #8 PEM), made the first time it is asked for and
  // never sent anywhere: POS gets only the public half and a signature by it (8.2)
  localKey() {
    const path = join(this.dir, "tls", "key.pem");
    const had = readOrNone(path);
    if (had !== void 0) return had;
    const made = generateKeyPairSync("ec", { namedCurve: "prime256v1" }).privateKey.export({ format: "pem", type: "pkcs8" }).toString();
    written(path, made, PRIVATE);
    return made;
  }
  // The local path's certificate chain, beside its key, which stays in key.pem alone
  certificate() {
    const cert = readOrNone(join(this.dir, "tls", "local.pem"));
    return cert === void 0 ? void 0 : { cert, key: this.localKey() };
  }
  setCertificate(cert) {
    written(join(this.dir, "tls", "local.pem"), cert);
  }
  #sale(sale, kind) {
    return join(this.dir, "journal", `${sale}.${kind}`);
  }
  knows(sale) {
    return readOrNone(this.#sale(sale, "sale")) !== void 0 || readOrNone(this.#sale(sale, "acked")) !== void 0;
  }
  putSale(sale, raw) {
    written(this.#sale(sale, "sale"), raw);
  }
  ackSale(sale) {
    try {
      renameSync(this.#sale(sale, "sale"), this.#sale(sale, "acked"));
    } catch {
      return;
    }
  }
  // The sales not yet acked, oldest first, as the journal messages offered for them; an acked one's mark kept past the
  // days gone. One not acked is kept however old, since it may be the only copy of a sale left (an iPad wiped through
  // a long outage), until POS acks it. Each is aged by when the box received it, as its record says, so a file's own
  // time (moved by a restore or a copy) counts only for one that can't be read.
  sales(days, now) {
    const kept = [];
    for (const name of readdirSync(join(this.dir, "journal"))) {
      const m = UUID_FILE.exec(name);
      const path = join(this.dir, "journal", name);
      if (m === null) continue;
      const j = this.#journal(path);
      const at = j?.receivedAt ?? statSync(path).mtimeMs;
      if (m[2] === "sale") kept.push({ sale: m[1] ?? "", raw: j?.raw ?? "", at });
      else if (now - at > days * DAY_MS) unlinkSync(path);
    }
    return kept.filter((k) => k.raw !== "").sort((a, b) => a.at - b.at).map(({ sale, raw }) => ({ sale, raw }));
  }
  #journal(path) {
    const raw = readOrNone(path) ?? "";
    try {
      const m = fromBox(raw);
      return m.type === "journal" ? { raw, receivedAt: m.receivedAt } : void 0;
    } catch (err) {
      log("sale_unreadable", { error: String(err) });
      return void 0;
    }
  }
};

// box/version.ts
var BOX_VERSION = "1.7.0";

// box/program.ts
var TIMING = {
  link: { firstMs: 1e3, maxMs: 3e4, steadyMs: 6e4 },
  scanner: { firstMs: 1e3, maxMs: 3e5, steadyMs: 6e4, debounceMs: 300 },
  printer: { connectForMs: 1e4, attemptMs: 2e3, statusMs: 1e3, processedMs: 3e4 },
  ackMs: 15e3,
  helloMs: 5e3,
  pollMs: 6e4,
  find: { answerMs: 500, atOnce: 8, firstMs: 5e3, everyMs: 9e5 }
};
var PRIVATE_V4 = /^(?:10\.|172\.(?:1[6-9]|2\d|3[01])\.|192\.168\.)/;
var BRIDGE_V4 = /^(?:172\.17\.|172\.30\.3[23]\.)/;
var onShopNetwork = (address2) => PRIVATE_V4.test(address2) && !BRIDGE_V4.test(address2);
function shopAddress() {
  let routes;
  try {
    routes = readFileSync2("/proc/net/route", "utf8").split("\n").slice(1).map((l) => l.trim().split(/\s+/));
  } catch {
    return void 0;
  }
  const out = routes.find((r) => r[1] === "00000000")?.[0];
  return out === void 0 ? void 0 : networkInterfaces()[out]?.find((i) => i.family === "IPv4" && onShopNetwork(i.address))?.address;
}
var KEY_PATH = `${SOCKETS.box}/key`;
var PAIR_MS = 15e3;
var JOURNAL_DAYS = 7;
var PRUNE_MS = 36e5;
var JOURNAL_WINDOW = 20;
var JOURNAL_MAX = 1e5;
var SCANS_MAX = 1e3;
var SENDS = 3;
function written2(m) {
  const raw = JSON.stringify(m.type === "journal" ? { ...m, body: Object.fromEntries(m.body), window: m.window === null ? null : Object.fromEntries(m.window) } : m);
  fromBox(raw);
  return raw;
}
var jobAnswer = (job, o) => written2({ type: "job", job, ...o });
function relayed(raw, now) {
  const m = fromBox(raw);
  return m.type === "journal" ? written2({ ...m, sentAt: now }) : raw;
}
function keyForm(key) {
  const m = fromBox(JSON.stringify({ type: "hello", key, version: BOX_VERSION }));
  if (m.type !== "hello") throw new Error("not a hello");
  return m.key;
}
function certificateError(c) {
  try {
    if (!new X509Certificate(c.cert).checkPrivateKey(createPrivateKey(c.key))) return "the key is not the certificate's";
    createSecureContext(c);
    return void 0;
  } catch (err) {
    return String(err);
  }
}
var Box = class {
  #store;
  #scans = new Pending({ window: SCANS_MAX, max: SCANS_MAX, sends: SENDS });
  #sales = new Pending({ window: JOURNAL_WINDOW, max: JOURNAL_MAX, sends: SENDS });
  #scanners = /* @__PURE__ */ new Map();
  #printers;
  #link;
  #timers;
  #settings;
  #local;
  // Certificates taken one after another, the local server started by the first
  #tls = Promise.resolve();
  #status = "";
  #finding = false;
  #findSoon;
  c;
  constructor(c) {
    this.c = c;
    this.#store = new Store(c.data);
    this.#printers = new Printers({ timing: c.timing.printer, pollMs: c.timing.pollMs, changed: () => this.#changed() });
    this.#prune();
    this.#link = new Link({
      url: `${c.origin.replace(/^http/, "ws")}${SOCKETS.box}`,
      dial: c.dial,
      timing: c.timing.link,
      hello: () => this.#hello(),
      opened: () => this.#opened(),
      message: (raw) => this.#fromPos(raw),
      closed: () => this.#closed()
    });
    this.#timers = [
      setInterval(() => this.#watch(), Math.min(1e3, c.timing.ackMs)),
      setInterval(() => this.#prune(), PRUNE_MS),
      setInterval(() => void this.#find(), c.timing.find.everyMs)
    ];
    const settings = this.#store.settings();
    if (settings !== void 0) this.#apply(settings);
  }
  // The local path, once the box holds a certificate for it
  async start() {
    const c = this.#store.certificate();
    if (c !== void 0) this.#certificate(c, false);
    await this.#tls;
  }
  localPort() {
    return this.#local?.port();
  }
  // A one-time code given to the add-on, exchanged at POS for the box's key
  async pair(code) {
    try {
      const res = await fetch(`${this.c.origin}${KEY_PATH}`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ code: keyForm(code) }),
        signal: AbortSignal.timeout(PAIR_MS)
      });
      const answer = res.ok ? await res.json() : void 0;
      this.#store.setKey(keyForm(fields(answer, "answer", ["key"]).get("key")));
    } catch (err) {
      log("pair_failed", { error: String(err) });
      return false;
    }
    log("paired");
    this.#link.restart();
    return true;
  }
  async stop() {
    for (const t of this.#timers) clearInterval(t);
    clearTimeout(this.#findSoon);
    this.#link.stop();
    for (const w of this.#scanners.values()) w.stop();
    this.#printers.stop();
    await this.#tls;
    await this.#local?.close();
  }
  #hello() {
    const key = this.#store.key();
    if (key === void 0) return void 0;
    try {
      return written2({ type: "hello", key, version: BOX_VERSION });
    } catch (err) {
      log("key_unreadable", { error: String(err) });
      return void 0;
    }
  }
  #statusNow() {
    const scanners = [...this.#scanners].map(([id, w]) => ({ id, state: w.connected ? "connected" : "disconnected" }));
    return written2({ type: "status", scanners, printers: this.#printers.states(), internet: this.#link.isOpen() });
  }
  #registers() {
    return this.#local?.registers() ?? [];
  }
  #changed(force = false) {
    const raw = this.#statusNow();
    if (raw === this.#status && !force) return;
    this.#status = raw;
    this.#link.send(raw);
    for (const r of this.#registers()) r.send(raw);
  }
  #flush() {
    if (!this.#link.isOpen()) return;
    const now = Date.now();
    for (const raw of [...this.#scans.next(now), ...this.#sales.next(now).map((r) => relayed(r, now))]) this.#link.send(raw);
  }
  #opened() {
    this.#changed(true);
    this.#flush();
    clearTimeout(this.#findSoon);
    this.#findSoon = setTimeout(() => void this.#find(), this.c.timing.find.firstMs);
  }
  // The scanners and printers on the shop's network the box doesn't drive yet, told to POS, which lists them for the
  // owner to set up; one look at a time, and only while POS hears it
  async #find() {
    const own = (this.c.address ?? shopAddress)();
    if (this.#finding || own === void 0 || !this.#link.isOpen()) return;
    this.#finding = true;
    try {
      const known = new Set([...this.#settings?.scanners ?? [], ...this.#settings?.printers ?? []].map((d) => `${d.host}:${String(d.port)}`));
      const found = await findOnNetwork(own, known, this.c.timing.find);
      if (this.#link.isOpen()) this.#link.send(written2({ type: "found", ...found }));
    } catch (err) {
      log("find_failed", { error: String(err) });
    } finally {
      this.#finding = false;
    }
  }
  // A sale given up on stays on disk and is offered again at the next prune
  #closed() {
    for (const scan of this.#scans.unsent()) log("scan_given_up", { scan });
    for (const sale of this.#sales.unsent()) log("sale_given_up", { sale });
    this.#changed();
  }
  #watch() {
    const now = Date.now();
    if (!this.#link.isOpen() || !(this.#scans.overdue(now, this.c.timing.ackMs) || this.#sales.overdue(now, this.c.timing.ackMs))) return;
    log("ack_overdue");
    this.#link.drop();
  }
  #prune() {
    for (const s of this.#store.sales(JOURNAL_DAYS, Date.now())) this.#sales.add(s.sale, s.raw);
  }
  #scanned(scanner, register, code) {
    const id = randomUUID();
    const raw = written2({ type: "scan", id, scanner, code, at: (/* @__PURE__ */ new Date()).toISOString() });
    for (const r of this.#registers()) if (this.#settings?.registers.find((x) => x.id === r.id)?.register === register) r.send(raw);
    this.#scans.add(id, raw);
    this.#flush();
  }
  #fromPos(raw) {
    let m;
    try {
      m = toBox(raw);
    } catch (err) {
      log("pos_refused", { error: String(err) });
      return;
    }
    switch (m.type) {
      case "print":
      case "drawer":
        void this.#printers.job(m).then((o) => this.#link.send(jobAnswer(m.job, o)));
        return;
      case "ack":
        if (m.of === "scan") this.#scans.ack(m.id);
        else {
          this.#sales.ack(m.id);
          this.#store.ackSale(m.id);
        }
        this.#flush();
        return;
      case "settings":
        this.#store.setSettings(raw);
        this.#apply(m);
        this.#askCertificate(m.name);
        return;
      case "certificate":
        this.#certificate({ cert: m.cert, key: this.#store.localKey() }, true);
        return;
      case "hello":
      case "journal":
        log("pos_unexpected", { type: m.type });
    }
  }
  #fromRegister(r, m) {
    switch (m.type) {
      case "print":
      case "drawer": {
        const own = this.#settings?.registers.find((x) => x.id === r.id)?.printer;
        void (own === m.printer ? this.#printers.job(m) : Promise.resolve({ state: "failed", reason: "error" })).then((o) => r.send(jobAnswer(m.job, o)));
        return;
      }
      case "journal": {
        if (this.#store.knows(m.sale)) return;
        const now = Date.now();
        const raw = written2({ type: "journal", sale: m.sale, register: r.id, body: m.body, at: m.at, window: m.window, sig: m.sig, receivedAt: now, sentAt: now });
        this.#store.putSale(m.sale, raw);
        this.#sales.add(m.sale, raw);
        this.#flush();
        return;
      }
      case "ack":
        return;
    }
  }
  #apply(s) {
    this.#settings = s;
    for (const [id, w] of this.#scanners) {
      if (s.scanners.some((x) => JSON.stringify(x) === w.key)) continue;
      w.stop();
      this.#scanners.delete(id);
    }
    for (const x of s.scanners.filter((x2) => !this.#scanners.has(x2.id))) {
      const w = { key: JSON.stringify(x), register: x.register, connected: false, stop: () => void 0 };
      w.stop = watchScanner(x, this.c.timing.scanner, {
        code: (code) => this.#scanned(x.id, x.register, code),
        connected: (up) => {
          w.connected = up;
          this.#changed();
        }
      }).stop;
      this.#scanners.set(x.id, w);
    }
    for (const r of this.#registers()) if (s.registers.find((x) => x.id === r.id)?.keyHash !== r.keyHash) r.close();
    this.#printers.apply(s);
  }
  // The local path's certificate asked for its name, by the box's own key: the request's information signed here, so
  // the key never leaves the box; asked on each settings, as POS reads the certificate the platform renewed for it
  #askCertificate(name) {
    const address2 = (this.c.address ?? shopAddress)();
    if (name === null || address2 === void 0) return;
    const key = createPrivateKey(this.#store.localKey());
    const spki = createPublicKey(key).export({ format: "der", type: "spki" });
    this.#link.send(written2({ type: "csr", address: address2, key: spki.toString("base64"), signature: sign("sha256", csrInfo(name, spki), key).toString("base64") }));
  }
  #certificate(c, fresh) {
    this.#tls = this.#tls.then(() => this.#take(c, fresh)).catch((err) => log("local_path_failed", { error: String(err) }));
  }
  async #take(c, fresh) {
    const error = certificateError(c);
    if (error !== void 0) {
      log("certificate_refused", { error });
      return;
    }
    if (fresh) this.#store.setCertificate(c.cert);
    if (this.#local !== void 0) this.#local.setCertificate(c);
    else if (this.c.upgrade !== void 0) await this.#serve(c, this.c.upgrade);
  }
  async #serve(certificate, upgrade) {
    this.#local = await serveLocal({
      port: this.c.localPort,
      certificate,
      origin: this.c.origin,
      upgrade,
      helloMs: this.c.timing.helloMs,
      keyHash: (register) => this.#settings?.registers.find((x) => x.id === register)?.keyHash,
      joined: (r) => r.send(this.#statusNow()),
      message: (r, m) => this.#fromRegister(r, m)
    });
  }
};

// box/place.ts
var DATA = "/data";
var IPV4 = /^(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)$/;
var COMPANY = /^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$/;
function boxPlace(env, options) {
  const named = env["BOX_COMPANY"];
  if (named?.trim() === "") return void 0;
  const company2 = named ?? fields(parseJson(options(), "options"), "options", ["company"]).get("company");
  const lan2 = env["BOX_LAN_IP"] === void 0 || env["BOX_LAN_IP"] === "" ? void 0 : text(env["BOX_LAN_IP"], "BOX_LAN_IP", { max: 15, pattern: IPV4 });
  if (lan2 !== void 0 && !onShopNetwork(lan2)) throw new Invalid("BOX_LAN_IP: not an address on the shop's network");
  return { company: text(company2, "company", { max: 253, pattern: COMPANY }), data: env["BOX_DATA"] ?? DATA, lan: lan2 };
}

// box/socket.ts
var dialWebSocket = (url, h) => {
  const ws = new WebSocket(url);
  let heard;
  ws.onopen = () => {
    heard = h.opened({ send: (raw) => ws.send(raw), close: () => ws.close() });
  };
  ws.onmessage = (e) => {
    const data2 = e.data;
    if (typeof data2 === "string") heard?.message(data2);
    else ws.close();
  };
  ws.onclose = () => {
    if (heard === void 0) h.failed();
    else heard.closed();
  };
};

// ../../node_modules/ws/wrapper.mjs
var import_stream = __toESM(require_stream(), 1);
var import_extension = __toESM(require_extension(), 1);
var import_permessage_deflate = __toESM(require_permessage_deflate(), 1);
var import_receiver = __toESM(require_receiver(), 1);
var import_sender = __toESM(require_sender(), 1);
var import_subprotocol = __toESM(require_subprotocol(), 1);
var import_websocket = __toESM(require_websocket(), 1);
var import_websocket_server = __toESM(require_websocket_server(), 1);

// box/upgrade.ts
var upgradeWs = (req, socket, head, max, opened2) => {
  new import_websocket_server.default({ noServer: true, maxPayload: 4 * max }).handleUpgrade(req, socket, head, (ws) => {
    const heard = opened2({ send: (raw) => ws.send(raw), close: () => ws.close() });
    ws.on("message", (data2, binary) => binary ? ws.close(1003) : heard.message(data2.toString()));
    ws.on("close", () => heard.closed());
    ws.on("error", () => ws.terminate());
  });
};

// box/main.ts
var LOCAL_PORT = 8443;
var EX_CONFIG = 78;
var place;
try {
  place = boxPlace(process.env, () => readFileSync3("/data/options.json", "utf8"));
} catch (err) {
  process.stderr.write(`box configuration refused: ${err instanceof Error ? err.message : String(err)}
`);
  process.exit(EX_CONFIG);
}
if (place === void 0) process.exit(0);
var { company, data, lan } = place;
var box = new Box({
  origin: `https://${company}`,
  data,
  localPort: LOCAL_PORT,
  dial: dialWebSocket,
  upgrade: upgradeWs,
  timing: TIMING,
  ...lan === void 0 ? {} : { address: () => lan }
});
process.once("SIGTERM", () => void box.stop().then(() => process.exit(0)));
await box.start();
for await (const line of createInterface({ input: process.stdin })) if (line.trim() !== "") await box.pair(line.trim());
