import tensorflow as tf
import math
import numpy as np
import sklearn
import string
import re
import collections


class RNNTextGen:
    def __init__(self, sess, text, seq_len=50, cell_size=128, n_layer=3, stateful=True,
                 n_filters=64, kernel_size=5, pool_size=4, padding='VALID'):
        """
        Parameters:
        -----------
        sess: object
            tf.Session() object
        text: string
            corpus
        seq_len: int
            Sequence length
        cell_size: int
            Number of units in the rnn cell
        n_layers: int
            Number of layers of stacked rnn cells
        stateful: boolean
            Whether state will be shared
        """
        self.sess = sess
        self.text = text
        self.seq_len = seq_len
        self.n_filters = n_filters
        self.kernel_size = kernel_size
        self.pool_size = pool_size
        self.padding = padding
        self.cell_size = cell_size
        self.n_layer = n_layer
        self.stateful = stateful
        self.current_layer = None

        self.text_preprocessing()
        self.build_graph()
    # end constructor


    def text_preprocessing(self):
        self.text = self.clean_text(self.text)
        all_word_list = list(self.text)

        self.word2idx, self.idx2word = self.build_vocab(all_word_list)
        self.vocab_size = len(self.idx2word)
        print('Vocabulary length:', self.vocab_size)
        assert len(self.idx2word) == len(self.word2idx), "len(idx2word) is not equal to len(word2idx)"

        self.all_word_idx = self.convert_text_to_idx(all_word_list, self.word2idx)
    # end method text_preprocessing


    def build_graph(self):
        with tf.variable_scope('main_model'):
            self.add_input_layer()       
            self.add_word_embedding_layer()
            self.add_lstm_cells()
            self.add_dynamic_rnn()
            self.reshape_rnn_out()
            self.add_output_layer()
            self.add_backward_path()
        with tf.variable_scope('main_model', reuse=True):
            self.add_sample_model()
    # end method build_graph


    def add_input_layer(self):
        self.batch_size = tf.placeholder(tf.int32)
        self.X = tf.placeholder(tf.int32, [None, self.seq_len])
        self.Y = tf.placeholder(tf.int32, [None, self.seq_len])
        self.W = tf.get_variable('W', [self.cell_size, self.vocab_size], tf.float32,
                                  tf.contrib.layers.variance_scaling_initializer())
        self.b = tf.get_variable('b', [self.vocab_size], tf.float32, tf.constant_initializer(0.0))   
        self.in_keep_prob = tf.placeholder(tf.float32)
        self.current_layer = self.X
    # end method add_input_layer


    def add_word_embedding_layer(self):
        # (batch_size, seq_len) -> (batch_size, seq_len, n_hidden)
        E = tf.get_variable('E', [self.vocab_size, self.cell_size], tf.float32, tf.random_normal_initializer())
        self.current_layer = tf.nn.embedding_lookup(E, self.current_layer)
    # end method add_word_embedding_layer


    def add_conv1d(self, name, filter_shape, stride=1):
        W = self._W(name+'_w', filter_shape)
        b = self._b(name+'_b', [filter_shape[-1]])                                
        conv = tf.nn.conv1d(self.current_layer, W, stride=stride, padding=self.padding)
        conv = tf.nn.bias_add(conv, b)
        conv = tf.nn.relu(conv)
        self.current_layer = conv
        if self.padding == 'VALID':
            self.current_seq_len = int((self.current_seq_len-self.kernel_size+1) / stride)
        if self.padding == 'SAME':
            self.current_seq_len = int(self.current_seq_len / stride)
    # end method add_conv1d_layer


    def add_maxpool(self, k=2):
        conv = tf.expand_dims(self.current_layer, 1)
        conv = tf.nn.max_pool(conv, ksize=[1,1,k,1], strides=[1,1,k,1], padding=self.padding)
        conv = tf.squeeze(conv)
        self.current_layer = conv
        self.current_seq_len = int(self.current_seq_len / k)
    # end method add_global_maxpool_layer


    def add_lstm_cells(self):
        cell = tf.contrib.rnn.BasicLSTMCell(self.cell_size)
        cell = tf.contrib.rnn.DropoutWrapper(cell, self.in_keep_prob)
        self.cells = tf.contrib.rnn.MultiRNNCell([cell] * self.n_layer)
    # end method add_rnn_cells


    def add_dynamic_rnn(self):
        self.init_state = self.cells.zero_state(self.batch_size, tf.float32)
        self.current_layer, self.final_state = tf.nn.dynamic_rnn(self.cells, self.current_layer,
                                                                 initial_state=self.init_state,
                                                                 time_major=False)    
    # end method add_dynamic_rnn


    def reshape_rnn_out(self):
        self.current_layer = tf.reshape(self.current_layer, [-1, self.cell_size])
    # end method add_rnn_out


    def add_output_layer(self):
        self.logits = tf.nn.bias_add(tf.matmul(self.current_layer, self.W), self.b)
    # end method add_output_layer


    def add_backward_path(self):
        losses = tf.contrib.seq2seq.sequence_loss(
            logits = tf.reshape(self.logits, [self.batch_size, self.seq_len, self.vocab_size]),
            targets = self.Y,
            weights = tf.ones([self.batch_size, self.seq_len]),
            average_across_timesteps = True,
            average_across_batch = True,
        )
        self.loss = tf.reduce_sum(losses)
        self.lr = tf.placeholder(tf.float32)
        # gradient clipping
        gradients, _ = tf.clip_by_global_norm(tf.gradients(self.loss, tf.trainable_variables()), 5.0)
        optimizer = tf.train.AdamOptimizer(self.lr)
        self.train_op = optimizer.apply_gradients(zip(gradients, tf.trainable_variables()))
    # end method add_backward_path


    def add_sample_model(self):
        self._X = tf.placeholder(tf.int32, [None, 1])
        _W = tf.get_variable('W')
        _b = tf.get_variable('b')
        _E = tf.nn.embedding_lookup(tf.get_variable('E'), self._X)
        self._init_state = self.cells.zero_state(self.batch_size, tf.float32)
        rnn_out, self._final_state = tf.nn.dynamic_rnn(self.cells, _E,
                                                       initial_state=self._init_state,
                                                       time_major=False)
        rnn_out = tf.reshape(rnn_out, [-1, self.cell_size])
        logits = tf.nn.bias_add(tf.matmul(rnn_out, _W), _b)
        self._softmax_out = tf.nn.softmax(logits)
    # end add_sample_model


    def decrease_lr(self, en_exp_decay, global_step, n_epoch, nb_batch):
        if en_exp_decay:
            max_lr = 0.003
            min_lr = 0.0001
            decay_rate = math.log(min_lr/max_lr) / (-n_epoch*nb_batch)
            lr = max_lr*math.exp(-decay_rate*global_step)
        else:
            lr = 0.0005
        return lr
    # end method adjust_lr


    def _W(self, name, shape):
        return tf.get_variable(name, shape, tf.float32, tf.truncated_normal_initializer(stddev=0.1))
    # end method _W


    def _b(self, name, shape):
        return tf.get_variable(name, shape, tf.float32, tf.constant_initializer(0.1))
    # end method _b


    def clean_text(self, text):
        text = text.replace('\n', ' ')
        punctuation = string.punctuation
        punctuation = ''.join([x for x in punctuation if x not in ['-', "'"]])
        text = re.sub(r'[{}]'.format(punctuation), ' ', text)
        text = re.sub('\s+', ' ', text ).strip().lower()
        return text
    # end method clean_text()


    def build_vocab(self, word_list, min_word_freq=None):
        word_counts = collections.Counter(word_list)
        if min_word_freq is not None:
            word_counts = {key:val for key,val in word_counts.items() if val > min_word_freq}
        words = word_counts.keys()
        word2idx = {key:(idx+1) for idx,key in enumerate(words)} # create word -> index mapping
        word2idx['_unknown'] = 0 # add unknown key -> 0 index
        idx2word = {val:key for key,val in word2idx.items()} # create index -> word mapping
        return(word2idx, idx2word)
    # end method build_vocab()


    def convert_text_to_idx(self, all_word_list, word2idx):
        all_word_idx = []
        for word in all_word_list:
            try:
                all_word_idx.append(word2idx[word])
            except:
                all_word_idx.append(0)
        return all_word_idx
    # end method convert_text_to_idx()


    def learn(self, prime_texts=None, text_iter_step=3, num_gen=200, temperature=1.0, 
              n_epoch=25, batch_size=128, en_exp_decay=True, en_shuffle=False, keep_prob=1.0):
        
        X = np.array([self.all_word_idx[i:i+self.seq_len] for i in range(
            0, len(self.all_word_idx)-self.seq_len, text_iter_step)])
        Y = np.roll(X, -1, axis=1)
        Y[np.arange(len(X)-1), -1] = X[np.arange(1,len(X)), 0]
        print('X shape:', X.shape, 'Y shape:', Y.shape)

        if prime_texts is None:
            prime_texts = []
            for _ in range(3):
                random_start = np.random.randint(0, len(self.text)-1-self.seq_len)
                prime_texts.append(self.text[random_start: random_start + self.seq_len])
        
        log = {'loss': []}
        global_step = 0
        self.sess.run(tf.global_variables_initializer()) # initialize all variables
        
        for epoch in range(n_epoch):
            next_state = self.sess.run(self.init_state, feed_dict={self.batch_size:batch_size})
            batch_count = 1
            if en_shuffle:
                X, Y = sklearn.utils.shuffle(X, Y)
            for X_batch, Y_batch in zip(self.gen_batch(X, batch_size), self.gen_batch(Y, batch_size)):
                lr = self.decrease_lr(en_exp_decay, global_step, n_epoch, int(len(X)/batch_size))
                if (self.stateful) and (len(X_batch) == batch_size):
                    _, loss, next_state = self.sess.run([self.train_op, self.loss, self.final_state],
                                                         feed_dict={self.X:X_batch, self.Y:Y_batch,
                                                                    self.init_state:next_state,
                                                                    self.batch_size:len(X_batch), self.lr:lr,
                                                                    self.in_keep_prob:keep_prob})
                else:
                    _, loss = self.sess.run([self.train_op, self.loss],
                                             feed_dict={self.X:X_batch, self.Y:Y_batch,
                                                        self.batch_size:len(X_batch), self.lr:lr,
                                                        self.in_keep_prob:keep_prob})
                if batch_count % 10 == 0:
                    print ('Epoch %d/%d | Batch %d/%d | train loss: %.4f | lr: %.4f' % (epoch+1, n_epoch,
                    batch_count, (len(X)/batch_size), loss, lr))
                log['loss'].append(loss)
                batch_count += 1
                global_step += 1
            
            for prime_text in prime_texts:
                print(self.sample(prime_text, num_gen, temperature), end='\n\n')
            
        return log
    # end method fit


    def sample(self, prime_text, num_gen, temperature):
        # warming up
        next_state = self.sess.run(self._init_state, feed_dict={self.batch_size:1})
        word_list = list(prime_text)
        for word in word_list[:-1]:
            x = np.zeros([1,1])
            x[0,0] = self.word2idx[word] 
            next_state = self.sess.run(self._final_state, feed_dict={self._X:x,
                                                                     self._init_state:next_state,
                                                                     self.in_keep_prob:1.0})
        # end warming up

        out_sentence = prime_text + '|'
        word = word_list[-1]
        for n in range(num_gen):
            x = np.zeros([1,1])
            x[0,0] = self.word2idx[word]
            softmax_out, next_state = self.sess.run([self._softmax_out, self._final_state],
                                                     feed_dict={self._X:x,
                                                                self._init_state:next_state,
                                                                self.in_keep_prob:1.0})
            idx = self.infer_idx(softmax_out[0], temperature)
            if idx == 0:
                break
            word = self.idx2word[idx]
            out_sentence = out_sentence + word
        return(out_sentence)
    # end method sample


    def infer_idx(self, preds, temperature): # helper function to sample an index from a probability array
        preds = np.asarray(preds).astype('float64')
        preds = np.log(preds) / temperature
        exp_preds = np.exp(preds)
        preds = exp_preds / np.sum(exp_preds)
        probas = np.random.multinomial(1, preds, 1)
        return np.argmax(probas)
    # end method infer_idx


    def gen_batch(self, arr, batch_size):
        for i in range(0, len(arr), batch_size):
            yield arr[i : i+batch_size]
    # end method gen_batch
# end class