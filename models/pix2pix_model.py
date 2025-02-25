import torch
from .base_model import BaseModel
from . import networks
import os
import sys
from gansynth.normalizer import DataNormalizer


class Pix2PixModel(BaseModel):
    """ This class implements the pix2pix model, for learning a mapping from input images to output images given paired data.

    The model training requires '--dataset_mode aligned' dataset.
    By default, it uses a '--netG unet256' U-Net generator,
    a '--netD basic' discriminator (PatchGAN),
    and a '--gan_mode' vanilla GAN loss (the cross-entropy objective used in the orignal GAN paper).

    pix2pix paper: https://arxiv.org/pdf/1611.07004.pdf
    """
    @staticmethod
    def modify_commandline_options(parser, is_train=True):
        """Add new dataset-specific options, and rewrite default values for existing options.

        Parameters:
            parser          -- original option parser
            is_train (bool) -- whether training phase or test phase. You can use this flag to add training-specific or test-specific options.

        Returns:
            the modified parser.

        For pix2pix, we do not use image buffer
        The training objective is: GAN Loss + lambda_L1 * ||G(A)-B||_1
        By default, we use vanilla GAN loss, UNet with batchnorm, and aligned datasets.
        """
        # changing the default values to match the pix2pix paper (https://phillipi.github.io/pix2pix/)
        parser.set_defaults(norm='batch', netG='unet_256', dataset_mode='aligned')
        if is_train:
            parser.set_defaults(pool_size=0, gan_mode='vanilla')
            parser.add_argument('--lambda_L1', type=float, default=100.0, help='weight for L1 loss')
        #parser.add_argument('--loss_freq', type=int, default=50, help='frequency of saving loss plots')

        return parser

    def __init__(self, opt):
        """Initialize the pix2pix class.

        Parameters:
            opt (Option class)-- stores all the experiment flags; needs to be a subclass of BaseOptions
        """
        BaseModel.__init__(self, opt)
        # specify the training losses you want to print out. The training/test scripts will call <BaseModel.get_current_losses>
        #self.loss_names = ['G_GAN', 'G_L1', 'D_real', 'D_fake', 'Wasserstein', 'D', 'G', 'D_vgg', 'D_vgg_real',
        #                   'D_vgg_fake', 'D_vgg_Wasserstein', 'D_grad', 'D_vgg_grad', 'G_vgg']
        self.loss_names = ['G_GAN', 'G_L1', 'D_real', 'D_fake', 'Wasserstein', 'D', 'G', 'D_grad']
        # specify the images you want to save/display. The training/test scripts will call <BaseModel.get_current_visuals>
        self.visual_names = ['fake_B', 'real_B']
        # specify the models you want to save to the disk. The training/test scripts will call <BaseModel.save_networks> and <BaseModel.load_networks>
        if self.isTrain:
            self.model_names = ['G', 'D']
        else:  # during test time, only load G
            self.model_names = ['G']
        # define networks (both generator and discriminator)
        self.netG = networks.define_G(opt.input_nc, opt.output_nc, opt.ngf, opt.netG, convD=2, norm=opt.norm,
                                 init_type=opt.init_type, init_gain=opt.init_gain, gpu_ids=self.gpu_ids)
        # if len(self.gpu_ids) > 0:
        #     assert (torch.cuda.is_available())
        #     netG.to(self.gpu_ids[0])
        #     self.netG = torch.nn.DataParallel(netG, self.gpu_ids)

        if self.isTrain:  # define a discriminator; conditional GANs need to take both input and output images; Therefore, #channels for D is input_nc + output_nc
            #self.netD = networks.define_D(opt.input_nc + opt.output_nc, opt.ndf, opt.netD,
            #                              opt.n_layers_D, opt.norm, opt.init_type, opt.init_gain, self.gpu_ids)
            self.netD = networks.define_D(opt.input_nc + opt.output_nc, opt.ndf, opt.netD, 2,
                                           opt.n_layers_D, opt.norm, opt.init_type, opt.init_gain, self.gpu_ids)
            #self.netD_vgg = networks.define_D_vgg(opt.input_nc, self.gpu_ids, pretrained=True,
            #        pre_path="/home/cbd109/Users/hmq/codes/vgg16/vgg16-397923af.pth",  norm=opt.norm, use_dropout=opt.no_dropout)

        if self.isTrain:
            # define loss functions
            self.criterionGAN = networks.GANLoss(opt.gan_mode).to(self.device)
            self.criterionL1 = torch.nn.L1Loss()
            # initialize optimizers; schedulers will be automatically created by function <BaseModel.setup>.
            self.optimizer_G = torch.optim.Adam(self.netG.parameters(), lr=opt.g_lr, betas=(opt.beta1, 0.999))
            self.optimizer_D = torch.optim.Adam(self.netD.parameters(), lr=opt.d_lr, betas=(opt.beta1, 0.999))
            #self.optimizer_D_vgg = torch.optim.Adam(self.netD_vgg.parameters(), lr=0.00004, betas=(opt.beta1, 0.999))
            self.optimizers.append(self.optimizer_G)
            self.optimizers.append(self.optimizer_D)
            #self.optimizers.append(self.optimizer_D_vgg)

        self.normalizer = None

    def set_input(self, input):
        """Unpack input data from the dataloader and perform necessary pre-processing steps.

        Parameters:
            input (dict): include the data itself and its metadata information.

        The option 'direction' can be used to swap images in domain A and domain B.
        """
        AtoB = self.opt.direction == 'AtoB'

        #self.real_A = input['A' if AtoB else 'B'].to(self.device)
        self.real_A = self.normalizer.normalize(input['A' if AtoB else 'B'], 'seeg' if AtoB else 'eeg').to(self.device)
        #self.real_B = input['B' if AtoB else 'A'].to(self.device)
        self.real_B = self.normalizer.normalize(input['B' if AtoB else 'A'], 'eeg' if AtoB else 'seeg').to(self.device)
        self.image_paths = input['A_paths' if AtoB else 'B_paths']

    def set_normalizer(self, normalizer):
        self.normalizer = normalizer

    def update_batch_idx(self, batch_idx):
        self.batch_idx = batch_idx

    def forward(self):
        """Run forward pass; called by both functions <optimize_parameters> and <test>."""
        self.fake_B = self.netG(self.real_A.detach())  # G(A)

    def backward_D(self):
        """Calculate GAN loss for the discriminator"""
        # Fake; stop backprop to the generator by detaching fake_B !!!!! attach importance!!!!
        # fake.detach() sets fake_grad_fn to none, which enables it to be sent as input as a pure tensor
        # and avoids duplicate grad calculations
        # 注:real_A和B维度是n_batch,n_chan,height,width，所以上面的拼接是按channel拼，所以要求图size完全一样

        fake_AB = torch.cat((self.real_A, self.fake_B), 1)
        pred_fake = self.netD(fake_AB.detach())
        self.loss_D_fake = self.criterionGAN(pred_fake, False)
        # Real
        real_AB = torch.cat((self.real_A, self.real_B), 1)
        #real_AB = torch.cat((self.real_A, self.real_B), 2)
        pred_real = self.netD(real_AB.detach())
        self.loss_D_real = self.criterionGAN(pred_real, True)
        # WGANGP penalty and loss
        gradient_penalty, _ = networks.cal_gradient_penalty(self.netD, real_AB.detach(), fake_AB.detach(), self.device)
        self.loss_D_grad = gradient_penalty
        self.loss_Wasserstein = - (self.loss_D_real + self.loss_D_fake)
        # combine loss and calculate gradients
        self.loss_D = (self.loss_D_fake + self.loss_D_real) * 0.5 + gradient_penalty
        #self.loss_D.backward(retain_graph=True)
        self.loss_D.backward()

    def backward_D_vgg(self):
        pred_fake = self.netD_vgg(self.fake_B.detach())
        self.loss_D_vgg_fake = self.criterionGAN(pred_fake, False)
        pred_real = self.netD_vgg(self.real_B)
        self.loss_D_vgg_real = self.criterionGAN(pred_real, True)
        gradient_penalty, _ = networks.cal_gradient_penalty(self.netD_vgg, self.real_B.detach(), self.fake_B.detach(), self.device)
        self.loss_D_vgg_grad = gradient_penalty
        self.loss_D_vgg_Wasserstein = -(self.loss_D_vgg_real + self.loss_D_vgg_fake)
        self.loss_D_vgg = (self.loss_D_vgg_real + self.loss_D_vgg_fake) * 0.5 + gradient_penalty
        self.loss_D_vgg.backward()

    def backward_G(self):
        """Calculate GAN and L1 loss for the generator"""
        '''self.latent = self.netG.module.encoder(self.real_A.detach())  # 生成器encode出的中间变量
        self.pre_latent = self.netE_s(self.real_A)  # 预训练SEEG encoder输出的结果
        self.back_latent = self.netE_e(self.fake_B)'''

        # First, G(A) should fake the discriminator
        fake_AB = torch.cat((self.real_A, self.fake_B), 1)
        pred_fake = self.netD(fake_AB)
        self.loss_G_GAN = self.criterionGAN(pred_fake, True)
        #pred_fake_B = self.netD_vgg(self.fake_B)
        #self.loss_G_vgg = self.criterionGAN(pred_fake_B, True)
        # Second, G(A) = B
        self.loss_G_L1 = self.criterionL1(self.fake_B, self.real_B) * self.opt.lambda_L1
        # combine loss and calculate gradients
        self.loss_G = self.loss_G_GAN + self.loss_G_L1
        self.loss_G.backward()

    def optimize_parameters(self):

        n_alt = 3
        self.forward()  # compute fake images: G(A)
        self.set_requires_grad(self.netD, True)  # enable backprop for D
        # update D
        self.optimizer_D.zero_grad()  # set D's gradients to zero
        self.backward_D()  # calculate gradients for D
        self.optimizer_D.step()  # update D's weights
        # update G
        if self.batch_idx % n_alt == 0:
            self.set_requires_grad(self.netD, False)  # D requires no gradients when optimizing G
            self.optimizer_G.zero_grad()  # set G's gradients to zero
            self.backward_G()  # calculate graidents for G
            self.optimizer_G.step()  # udpate G's weights

    def set_VGG_requires_grad(self, net, requires_grad=True):
        for p in net.parameters():
            p.requires_grad = requires_grad

        if requires_grad:
            for p in list(net.parameters())[2: 6]:  # 第二个卷积到第8个卷积不训练
                p.requires_grad = False
