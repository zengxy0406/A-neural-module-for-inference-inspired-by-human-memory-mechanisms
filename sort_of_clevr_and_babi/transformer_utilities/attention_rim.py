import torch
import torch.nn as nn
import numpy as np
import random
from .sparse_attn import Sparse_attention
# from sparse_attn import Sparse_attention
import torch.nn.functional as F
from .GroupLinearLayer import GroupLinearLayer
from .sparse_grad_attn import Sparse_grad_attention
# from GroupLinearLayer import GroupLinearLayer
# from sparse_grad_attn import Sparse_grad_attention


class Identity_2(torch.autograd.Function):
    @staticmethod
    def forward(ctx, input):
        return input * 1.0
    def backward(ctx, grad_output):
        print(torch.sqrt(torch.sum(torch.pow(grad_output,2))))
        print('+++++++++')
        return grad_output * 1.0

class Identity(torch.autograd.Function):
    @staticmethod
    def forward(ctx, input):
        return input * 1.0
    def backward(ctx, grad_output):
        print(torch.sqrt(torch.sum(torch.pow(grad_output,2))))
        print('-----------')
        return grad_output * 1.0

#缩放注意力点乘
class ScaledDotProductAttention(nn.Module):
    '''
    Scaled Dot-Product Attention
    __init__: temperature, topk, grad_sparse=False, attn_dropout=0.1, flag=False
    input: q, k, v, mask
    output: output, attn, extra_loss
    '''

    def __init__(self, temperature, topk = -1, grad_sparse=False, attn_dropout=0.1, flag=False):
        super().__init__()
        self.temperature = temperature
        #self.dropout = nn.Dropout(attn_dropout)
        self.softmax = nn.Softmax(dim=2)    #行之和1
        self.grad_sparse = grad_sparse
        self.topk = topk
        self.sa = Sparse_attention(top_k=topk) #k=2
        self.flag = flag

    def forward(self, q, k, v, mask=None):

        # print("~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~Forward of Scaled Dot Product Attention~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~")
        # print("q: ", q.size())
        # print("k: ", k.size())
        # print("v: ", v.size())
        # print("k transpose: ", k.transpose(1,2).size())
        # input()
        # query 和 key 做矩阵乘法
        # k.transpose(1, 2)为将k的第二个维度和第三个维度进行交换
        attn = torch.bmm(q, k.transpose(1, 2))
        attn = attn / self.temperature

        #print('in forward attn shape', attn.shape)

        if mask is not None:
            # 用 -inf 填充掉 mask 对应的位置
            # mask 为与attn形状相同的掩码张量，里面true or false
            attn = attn.masked_fill(mask, -np.inf)

        if self.flag:
            n_b,k_1,k_2 = attn.size()
            # permute(0, 2, 1)将张量的后两个维度交换
            attn = self.softmax(attn.permute(0,2,1)).reshape(n_b,k_1,k_2)
        else:
            attn = self.softmax(attn)

        extra_loss = 0.0

        use_sparse = False#False

        # 是否使用稀疏性
        if use_sparse:
            mb, ins, outs = attn.shape[0], attn.shape[1], attn.shape[2]
            if self.flag:
                sparse_attn = attn.permute(0,2,1).reshape(mb*outs, ins)
            else:
                sparse_attn = attn.reshape((mb*ins, outs))
            #print('sparse attn shape 1', sparse_attn.shape)
            #sga = Sparse_grad_attention(2)

            # grad_sparse为稀疏梯度计算方法
            if self.grad_sparse:
                sga = Sparse_grad_attention(self.topk)
                sparse_attn = sga(sparse_attn)
            else:
                sparse_attn = self.sa(sparse_attn)
            if self.flag:
                sparse_attn = sparse_attn.reshape(mb, outs, ins).permute(0, 2, 1)
            else:
                sparse_attn = sparse_attn.reshape((mb,ins,outs))
            attn = sparse_attn*1.0

        # 使用注意力分布对 value 进行加权，得到输出
        output = torch.bmm(attn, v)

        return output, attn, extra_loss

import torch.nn.functional as F

class MultiHeadAttention(nn.Module):
    ''' Multi-Head Attention module '''
    # 原来参数为grad_sparse
    def __init__(self, n_head, d_model_read, d_model_write, d_model_out, d_k, d_v, grad_sparse, residual=True, dropout=0.1, skip_write=False, flag=False):
        super().__init__()

        self.n_head = n_head
        self.d_k = d_k
        self.d_v = d_v
        # zxy 原来没有grad_sparse
        # self.grad_sparse = grad_sparse

        # print("~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~Initialize Multi-Head Attention~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~")
        # print('d model read: ', d_model_read)
        # print('d_model_write: ', d_model_write)
        # print('d_model_out: ', d_model_out)
        # print('n_head: ', n_head)
        # print('d_k: ', d_k)
        # print('d_v: ', d_v)
        # print('num_blocks_read: ', num_blocks_read)
        # print('num_blocks_write: ', num_blocks_write)
        # input()

        self.GLN_qs = nn.Linear(d_model_read, n_head * d_k)
        self.GLN_ks = nn.Linear(d_model_write, n_head * d_k)
        self.GLN_vs = nn.Linear(d_model_write, n_head * d_v)

        self.residual = residual

        #self.w_qs = nn.Linear(d_model_read, n_head * d_k)
        #self.w_ks = nn.Linear(d_model_write, n_head * d_k)
        #self.w_vs = nn.Linear(d_model_write, n_head * d_v)

        #nn.init.normal_(self.w_qs.weight, mean=0, std=np.sqrt(2.0 / (d_model + d_k)))
        #nn.init.normal_(self.w_ks.weight, mean=0, std=np.sqrt(2.0 / (d_model + d_k)))
        #nn.init.normal_(self.w_vs.weight, mean=0, std=np.sqrt(2.0 / (d_model + d_v)))

        self.attention = ScaledDotProductAttention(temperature=np.power(d_k, 0.5), flag=flag)
        #self.layer_norm = nn.LayerNorm(d_model)

        self.gate_fc = nn.Linear(n_head * d_v, d_model_out)

        #是否跳过写操作
        if not skip_write:
            self.fc = nn.Linear(n_head * d_v, d_model_out)
        else:
            self.fc = lambda a: a

        #nn.init.xavier_normal_(self.fc.weight)

        self.dropout = nn.Dropout(dropout)
        # Layer Normalization 是一种正则化方法
        self.ln = nn.LayerNorm(d_model_out)

    def forward(self, q, k, v, mask=None):

        #print('attn input shape', q.shape)

        d_k, d_v, n_head = self.d_k, self.d_v, self.n_head

        sz_b, len_q, _ = q.size()
        sz_b, len_k, _ = k.size()
        sz_b, len_v, _ = v.size()

        residual = q

        #print('q shape', q.shape)

        # print("~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~Forward of Multi-Head Attention~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~")
        # print("q: ", q.size())
        # print("k: ", k.size())
        # print("v: ", v.size())
        # input()

        q = self.GLN_qs(q).view(sz_b, len_q, n_head, d_k)
        #q = self.w_qs(q).view(sz_b, len_q, n_head, d_k)
        k = self.GLN_ks(k).view(sz_b, len_k, n_head, d_k)
        v = self.GLN_vs(v).reshape(sz_b, len_v, n_head, d_v)
        #v = v.view(sz_b, len_v, n_head, d_v)

        # print("GLN q: ", q.size())
        # print("GLN k: ", k.size())
        # print("GLN v: ", v.size())

        q = q.permute(2, 0, 1, 3).contiguous().view(-1, len_q, d_k) # (n*b) x lq x dk
        k = k.permute(2, 0, 1, 3).contiguous().view(-1, len_k, d_k) # (n*b) x lk x dk
        v = v.permute(2, 0, 1, 3).contiguous().view(-1, len_v, d_v) # (n*b) x lv x dv

        # print("Permute q: ", q.size())
        # print("Permute k: ", k.size())
        # print("Permute v: ", v.size())

        #mask = mask.repeat(n_head, 1, 1) # (n*b) x .. x ..
        output, attn, extra_loss = self.attention(q, k, v, mask=None)

        # print("Output: ", output.size())
        # print("Attention: ", attn.size())

        output = output.view(n_head, sz_b, len_q, d_v)
        output = output.permute(1, 2, 0, 3).contiguous().view(sz_b, len_q, -1) # b x lq x (n*dv)

        # print("Here Output: ", output.size())

        #print('output shape before fc', output.shape)

        #TODO: probably shouldn't just apply residual layer in the forward pass.

        output_init = output*1.0

        output = self.dropout(self.fc(output_init))

        gate = torch.sigmoid(self.gate_fc(output_init))

        #output = self.layer_norm(gate * output + (1 - gate) * residual)
        #output = gate * output + (1 - gate) * residual

        if self.residual:
            output = gate * torch.tanh(output)
        else:
            #output = self.ln(output)
            pass

        # print("Final Output: ", output.size())

        #output

        #print('attn', attn[0])
        #print('output input diff', output - residual)

        return output, attn, extra_loss

class PositionwiseFeedForward(nn.Module):
    ''' A two-feed-forward-layer module '''

    def __init__(self, d_in, d_hid, dropout=0.1):
        super().__init__()
        self.w_1 = nn.Conv1d(d_in, d_hid, 1) # position-wise
        self.w_2 = nn.Conv1d(d_hid, d_in, 1) # position-wise
        self.layer_norm = nn.LayerNorm(d_in)
        self.dropout = nn.Dropout(dropout)

    def forward(self, x):
        residual = x
        output = x.transpose(1, 2)
        output = self.w_2(F.relu(self.w_1(output)))
        output = output.transpose(1, 2)
        output = self.dropout(output)
        output = self.layer_norm(output + residual)
        return output


class Seq2SeqAttention(nn.Module):
    def __init__(self, enc_hid_dim, dec_hid_dim):
        super().__init__()

        self.attn = nn.Linear(enc_hid_dim + dec_hid_dim, dec_hid_dim)
        self.v = nn.Linear(dec_hid_dim, 1, bias = False)

    def forward(self, hidden, encoder_outputs):

        #hidden = [batch size, dec hid dim]
        #encoder_outputs = [src len, batch size, enc hid dim * 2]

        batch_size = encoder_outputs.shape[1]
        src_len = encoder_outputs.shape[0]

        #repeat decoder hidden state src_len times
        hidden = hidden.unsqueeze(1).repeat(1, src_len, 1)

        encoder_outputs = encoder_outputs.permute(1, 0, 2)

        #hidden = [batch size, src len, dec hid dim]
        #encoder_outputs = [batch size, src len, enc hid dim * 2]

        energy = torch.tanh(self.attn(torch.cat((hidden, encoder_outputs), dim = 2)))

        #energy = [batch size, src len, dec hid dim]

        attention = self.v(energy).squeeze(2)

        #attention= [batch size, src len]

        return F.softmax(attention, dim=1)


if __name__ == "__main__":

    x = torch.randn((64,3,100))

    mha = MultiHeadAttention(n_head=8, d_model_read=100, d_model_write=1, d_model_out=1, d_k=64, d_v=64)

    out, attn = mha(x,x,x)

    print('out shape', out.shape)
